"""The loop that actually trades.

Runs on a background thread so the dashboard going down doesn't stop it.
Each pass:
  1. save an equity point
  2. reconcile the ledger with real MT5 positions (catches SL/TP fills)
  3. daily-loss circuit breaker
  4. manage open positions (strategy exit + ATR trailing stop)
  5. look for new setups on *closed* bars and execute
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

import MetaTrader5 as mt5

from config import Settings
from strategy import analyze

log = logging.getLogger("engine")


class Engine:
    def __init__(self, settings: Settings, gateway, ledger, risk) -> None:
        self.s = settings
        self.gw = gateway
        self.ledger = ledger
        self.risk = risk

        self.paused = False
        self.running = False
        self.status = "idle"
        self.last_error = ""
        self.notes: dict[str, str] = {}
        self.decisions: dict[str, dict] = {}

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_snapshot: datetime | None = None

    # ------------------------------------------------------------- control
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.running = True
        self.status = "running"
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        self.status = "stopped"

    def pause(self, why: str = "paused by user") -> None:
        self.paused = True
        self.status = why

    def resume(self) -> None:
        self.paused = False
        self.status = "running"

    def kill(self) -> list:
        self.pause("kill switch — all positions closed")
        return self.gw.close_all()

    # ---------------------------------------------------------------- loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
                self.status = "paused" if self.paused else "running"
            except Exception as exc:  # never let one bad tick kill the thread
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("engine step failed")
            self._stop.wait(self.s.loop_seconds)

    def step(self) -> None:
        account = self.gw.account()
        self._snapshot(account.equity)
        self._reconcile()

        if self.paused:
            return

        breaker = self.risk.daily_loss_hit()
        if not breaker.allowed:
            log.warning("circuit breaker: %s", breaker.reason)
            self.gw.close_all()
            self.pause(breaker.reason)
            return

        for base in self.s.symbols:
            try:
                self._handle(base)
            except Exception as exc:
                self.notes[base] = f"error: {exc}"
                log.exception("handling %s failed", base)

    def _snapshot(self, equity: float) -> None:
        minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if self._last_snapshot == minute:
            return
        self._last_snapshot = minute
        self.ledger.snapshot(equity)

    # ------------------------------------------------------------ bookkeeping
    def _reconcile(self) -> None:
        """If a position vanished, it was closed by SL/TP — record the result."""
        live = {p.ticket for p in self.gw.positions()}
        for row in self.ledger.open_trades():
            ticket = int(row["ticket"]) if row["ticket"] is not None else 0
            if ticket in live:
                continue
            pnl, exit_price = self.gw.deal_pnl(ticket)
            self.ledger.close_trade(ticket, exit_price, pnl, "broker close (SL/TP)")

    # ---------------------------------------------------------------- trade
    def _handle(self, base: str) -> None:
        df = self.gw.candles(base, 600)
        closed_bars = df.iloc[:-1]  # the last bar is still forming
        params = self.s.params_for(base)
        analysis = analyze(
            closed_bars,
            lookback=params["lookback"],
            exit_lookback=params["exit_lookback"],
            adx_min=params["adx_min"],
        )

        info = self.gw.symbol_info(base)
        tick = self.gw.tick(base)
        session = self.risk.session_ok()
        spread_pts = self.gw.spread_points(base)
        spread_ok = spread_pts <= self.s.max_spread_for(base)

        dec = {
            "symbol": base,
            "trend": "up" if analysis.trend_up else "down",
            "adx": round(analysis.adx, 1),
            "adx_min": params["adx_min"],
            "atr": round(analysis.atr, 2),
            "price": tick.bid,
            "trigger_up": round(analysis.dc_high, 2),
            "trigger_down": round(analysis.dc_low, 2),
            "spread": round(spread_pts, 1),
            "spread_ok": spread_ok,
            "session_ok": session.allowed,
            "session": session.reason or "London/NY open",
            "risk_pct": round(self.s.risk_for(base) * 100, 2),
            "stop_atr": params["min_stop_atr"],
            "rr": params["reward_risk"],
            "trail_atr": params["trail_atr"],
            "lookback": params["lookback"],
            "action": "",
        }
        self.decisions[base] = dec

        positions = self.gw.positions(base)
        if positions:
            dec["action"] = "managing open trade"
            self._manage(base, positions[0], analysis, params)
            return

        if analysis.side is None:
            dec["action"] = analysis.reason
            self.notes[base] = analysis.reason
            return

        check = self.risk.can_open(base)
        if not check.allowed:
            dec["action"] = check.reason
            self.notes[base] = check.reason
            return

        price = tick.ask if analysis.side == "buy" else tick.bid

        stop_distance = max(
            analysis.atr * params["min_stop_atr"],
            info.spread * info.point * 2,
        )
        if stop_distance <= 0:
            dec["action"] = "no ATR — skipped"
            self.notes[base] = "no ATR — skipped"
            return

        if analysis.side == "buy":
            sl = price - stop_distance
            tp = price + stop_distance * params["reward_risk"]
        else:
            sl = price + stop_distance
            tp = price - stop_distance * params["reward_risk"]

        volume = self.risk.position_size(base, price, sl)
        if volume <= 0:
            dec["action"] = "position size rounded to zero"
            self.notes[base] = "position size rounded to zero"
            return

        result = self.gw.open_market(base, analysis.side, volume, sl, tp)
        if not result.ok:
            dec["action"] = f"order rejected [{result.retcode}] {result.comment}"
            self.notes[base] = f"order rejected [{result.retcode}] {result.comment}"
            return

        self.ledger.open_trade(
            result.order, self.gw.symbol(base), analysis.side, volume,
            result.price or price, sl, tp, self.s.mode,
        )
        dec["action"] = f"opened {analysis.side} {volume}"
        self.notes[base] = f"opened {analysis.side} {volume} @ {price:.2f}"
        log.info("%s %s %.2f @ %.5f sl=%.5f tp=%.5f", base, analysis.side, volume, price, sl, tp)

    def _manage(self, base: str, position, analysis, params: dict | None = None) -> None:
        params = params or self.s.params_for(base)
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        tick = self.gw.tick(base)
        price = tick.bid if is_buy else tick.ask

        # 1. strategy says get out
        wants_exit = (is_buy and analysis.exit_long) or (not is_buy and analysis.exit_short)
        if wants_exit:
            result = self.gw.close_position(position)
            if result.ok:
                pnl, exit_price = self.gw.deal_pnl(position.ticket)
                self.ledger.close_trade(position.ticket, exit_price or price, pnl, "strategy exit")
                self.notes[base] = "closed on exit signal"
            return

        # 2. trail the stop once we are at least 1 ATR in profit
        if analysis.atr <= 0:
            return
        trail = analysis.atr * params["trail_atr"]
        point = self.gw.symbol_info(base).point

        if is_buy:
            if price - position.price_open < analysis.atr:
                return
            new_sl = price - trail
            if new_sl > position.sl + point and new_sl < price:
                self.gw.modify_sltp(position, new_sl, position.tp)
        else:
            if position.price_open - price < analysis.atr:
                return
            new_sl = price + trail
            if (position.sl == 0 or new_sl < position.sl - point) and new_sl > price:
                self.gw.modify_sltp(position, new_sl, position.tp)

"""Backtester. Deliberately pessimistic, and now faithful to the live engine:

  * entries at the NEXT bar's open (no look-ahead)
  * pays the spread on entry and exit
  * if a bar touches both stop and target, the STOP is assumed first
  * ATR trailing stop, same rule as engine.py (only after 1 ATR of profit)
  * one position at a time, fixed fractional risk

`run_backtest` prepares indicators then calls `simulate`.
The sweep optimiser calls `simulate` directly so indicators are computed once.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from strategy import prepare

START_EQUITY = 10_000.0
MIN_LOT = 0.01

DEFAULTS = {
    "min_stop_atr": 1.5,
    "reward_risk": 2.0,
    "trail_atr": 2.5,
}


@dataclass
class BacktestResult:
    symbol: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    equity_end: float = START_EQUITY
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    curve: list[dict] = field(default_factory=list)


def simulate(
    data: pd.DataFrame,
    value_per_unit: float,
    symbol: str,
    spread_price: float = 0.0,
    risk_pct: float = 0.005,
    min_stop_atr: float = 1.5,
    reward_risk: float = 2.0,
    trail_atr: float = 2.5,
    start_equity: float = START_EQUITY,
    record_curve: bool = True,
    entry_hours: tuple[int, int] | None = None,
) -> BacktestResult:
    """Run on an already-prepared DataFrame (must contain the indicator columns)."""
    result = BacktestResult(symbol=symbol)
    if len(data) < 50 or value_per_unit <= 0:
        return result

    o = data["open"].to_numpy(dtype=float)
    h = data["high"].to_numpy(dtype=float)
    lo = data["low"].to_numpy(dtype=float)
    c = data["close"].to_numpy(dtype=float)
    atr = data["atr"].to_numpy(dtype=float)
    long_sig = data["long_signal"].to_numpy(dtype=bool)
    short_sig = data["short_signal"].to_numpy(dtype=bool)
    exit_long = data["exit_long"].to_numpy(dtype=bool)
    exit_short = data["exit_short"].to_numpy(dtype=bool)
    times = data["time"].to_list() if record_curve else None
    hours = (
        pd.to_datetime(data["time"]).dt.hour.to_numpy(dtype=int)
        if entry_hours
        else None
    )

    equity = start_equity
    peak = equity
    gross_win = gross_loss = 0.0
    r_sum = 0.0
    pos: dict | None = None
    pending: tuple[str, float] | None = None
    curve: list[dict] = []

    for i in range(len(data)):
        # ---- 1. fill a pending signal at this bar's open ------------------
        if pending is not None and pos is None:
            side, atr_sig = pending
            pending = None
            stop_distance = max(atr_sig * min_stop_atr, spread_price * 2)
            if stop_distance > 0:
                if side == "buy":
                    entry = o[i] + spread_price / 2
                    stop = entry - stop_distance
                    target = entry + stop_distance * reward_risk
                else:
                    entry = o[i] - spread_price / 2
                    stop = entry + stop_distance
                    target = entry - stop_distance * reward_risk

                size = equity * risk_pct / (stop_distance * value_per_unit)
                size = max(MIN_LOT, round(size, 2))
                pos = {
                    "side": side, "entry": entry, "stop": stop, "target": target,
                    "size": size, "risk": stop_distance * size * value_per_unit,
                }

        # ---- 2. manage the open position ---------------------------------
        if pos is not None:
            exit_price = None
            direction = 1.0 if pos["side"] == "buy" else -1.0

            if pos["side"] == "buy":
                if lo[i] <= pos["stop"]:
                    exit_price = pos["stop"]
                elif h[i] >= pos["target"]:
                    exit_price = pos["target"]
                elif exit_long[i]:
                    exit_price = c[i]
            else:
                if h[i] >= pos["stop"]:
                    exit_price = pos["stop"]
                elif lo[i] <= pos["target"]:
                    exit_price = pos["target"]
                elif exit_short[i]:
                    exit_price = c[i]

            if exit_price is not None:
                pnl = (exit_price - pos["entry"]) * direction * pos["size"] * value_per_unit
                pnl -= spread_price * pos["size"] * value_per_unit
                equity += pnl
                result.trades += 1
                if pnl >= 0:
                    result.wins += 1
                    gross_win += pnl
                else:
                    result.losses += 1
                    gross_loss += abs(pnl)
                if pos["risk"] > 0:
                    r_sum += pnl / pos["risk"]
                pos = None
            else:
                # trail only once we are at least 1 ATR in profit (same as live)
                if pos["side"] == "buy" and c[i] - pos["entry"] >= atr[i]:
                    new_stop = c[i] - trail_atr * atr[i]
                    if new_stop > pos["stop"]:
                        pos["stop"] = new_stop
                elif pos["side"] == "sell" and pos["entry"] - c[i] >= atr[i]:
                    new_stop = c[i] + trail_atr * atr[i]
                    if new_stop < pos["stop"]:
                        pos["stop"] = new_stop

        # ---- 3. decide at the close, for tomorrow's open ------------------
        if pos is None and pending is None:
            in_session = entry_hours is None or (
                entry_hours[0] <= hours[i] < entry_hours[1]
            )
            if in_session and long_sig[i]:
                pending = ("buy", atr[i])
            elif in_session and short_sig[i]:
                pending = ("sell", atr[i])

        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak * 100
            result.max_drawdown_pct = max(result.max_drawdown_pct, drawdown)
        if record_curve:
            curve.append({"time": str(times[i]), "value": round(equity, 2)})

        if equity <= 0:
            break

    result.equity_end = equity
    result.pnl = equity - start_equity
    result.profit_factor = gross_win / gross_loss if gross_loss else float("inf")
    result.avg_r = r_sum / result.trades if result.trades else 0.0
    result.curve = curve
    return result


def run_backtest(
    df: pd.DataFrame,
    value_per_unit: float,
    settings,
    symbol: str,
    spread_price: float = 0.0,
    risk_pct: float | None = None,
    params: dict | None = None,
) -> BacktestResult:
    p = {**DEFAULTS, **(params or {})}
    prepared = prepare(
        df,
        fast=p.get("fast", 20),
        slow=p.get("slow", 50),
        lookback=p.get("lookback", 20),
        exit_lookback=p.get("exit_lookback", 10),
        adx_min=p.get("adx_min", 22.0),
    ).dropna(subset=["atr", "adx", "dc_high", "dc_low"])
    prepared = prepared.reset_index(drop=True)

    return simulate(
        prepared,
        value_per_unit=value_per_unit,
        symbol=symbol,
        spread_price=spread_price,
        risk_pct=risk_pct if risk_pct is not None else settings.risk_for(symbol),
        min_stop_atr=p["min_stop_atr"],
        reward_risk=p["reward_risk"],
        trail_atr=p["trail_atr"],
    )


def report(r: BacktestResult) -> str:
    win_rate = (r.wins / r.trades * 100) if r.trades else 0.0
    pf = "inf" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
    return "\n".join(
        [
            f"--- {r.symbol} ---",
            f"trades          : {r.trades}",
            f"win rate        : {win_rate:.1f}%  ({r.wins}W / {r.losses}L)",
            f"net P/L         : {r.pnl:,.2f}",
            f"return          : {r.pnl / START_EQUITY * 100:+.2f}%",
            f"max drawdown    : {r.max_drawdown_pct:.2f}%",
            f"profit factor   : {pf}",
            f"avg R per trade : {r.avg_r:+.3f}",
        ]
    )


if __name__ == "__main__":
    from config import settings
    from mt5_gateway import MT5Gateway

    base = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        allow_live=False, path=settings.mt5_path,
    )
    gw.connect()
    history = gw.candles(base, 20000)
    info = gw.symbol_info(base)
    spread = info.spread * info.point
    value = gw.value_per_price_per_lot(base)

    result = run_backtest(history, value, settings, base, spread)
    print(report(result))

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    pd.DataFrame(result.curve).to_csv(out_dir / f"{base}_{settings.timeframe}.csv", index=False)
    print(f"equity curve -> reports/{base}_{settings.timeframe}.csv")
    gw.shutdown()

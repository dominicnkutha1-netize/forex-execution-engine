"""Risk engine: the part that keeps you solvent.

Rules, in order:
  1. session / weekend filter
  2. daily loss circuit breaker
  3. max concurrent trades
  4. spread guard
  5. position size from a fixed risk % of equity
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from config import Settings


@dataclass
class RiskCheck:
    allowed: bool
    reason: str = ""


class RiskManager:
    def __init__(self, settings: Settings, gateway, ledger) -> None:
        self.s = settings
        self.gw = gateway
        self.ledger = ledger

    # ------------------------------------------------------------- filters
    def session_ok(self, now: datetime | None = None) -> RiskCheck:
        now = now or datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return RiskCheck(False, "weekend")
        if now.weekday() == 4 and now.hour >= self.s.skip_friday_after_utc:
            return RiskCheck(False, "friday close")
        if not (self.s.session_start_utc <= now.hour < self.s.session_end_utc):
            return RiskCheck(False, "outside London/NY session")
        return RiskCheck(True)

    def daily_loss_hit(self) -> RiskCheck:
        equity = self.gw.account().equity
        start = self.ledger.day_start_equity(equity)
        pnl = self.ledger.pnl_since(datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00"))
        limit = -abs(start * self.s.max_daily_loss)
        if pnl <= limit:
            return RiskCheck(False, f"daily loss limit hit ({pnl:.2f})")
        return RiskCheck(True)

    def can_open(self, symbol: str) -> RiskCheck:
        session = self.session_ok()
        if not session.allowed:
            return session

        daily = self.daily_loss_hit()
        if not daily.allowed:
            return daily

        open_trades = self.gw.positions()
        if len(open_trades) >= self.s.max_open_trades:
            return RiskCheck(False, f"max open trades ({self.s.max_open_trades})")

        if any(p.symbol == self.gw.symbol(symbol) for p in open_trades):
            return RiskCheck(False, "already in this symbol")

        spread = self.gw.spread_points(symbol)
        limit = self.s.max_spread_for(symbol)
        if spread > limit:
            return RiskCheck(False, f"spread too wide ({spread:.0f} pts > {limit:.0f})")

        return RiskCheck(True)

    # -------------------------------------------------------------- sizing
    def position_size(self, symbol: str, entry: float, stop: float) -> float:
        info = self.gw.symbol_info(symbol)
        distance = abs(entry - stop)
        if distance <= 0:
            return 0.0

        equity = self.gw.account().equity
        risk_money = equity * self.s.risk_for(symbol)
        value_per_unit = self.gw.value_per_price_per_lot(symbol)
        raw = risk_money / (distance * value_per_unit)

        step = info.volume_step or 0.01
        lots = max(info.volume_min, min(info.volume_max, round(raw / step) * step))
        return round(lots, 8)

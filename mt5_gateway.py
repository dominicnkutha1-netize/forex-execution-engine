"""Everything that talks to the MetaTrader 5 terminal lives here.

Python talks to the *terminal*, the terminal talks to Exness.
So MT5 must be installed, running, and logged in before this module works.
"""
from __future__ import annotations

from dataclasses import dataclass

import MetaTrader5 as mt5
import pandas as pd

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# Exness uses suffixes depending on account type ("EURUSDm", "XAUUSDm", ...).
_SUFFIXES = ("", "m", ".a", ".raw", ".pro", ".c", ".z", "_")

_INVALID_FILL = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)


class GatewayError(RuntimeError):
    pass


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    order: int | None = None
    deal: int | None = None
    price: float | None = None
    comment: str = ""


class MT5Gateway:
    def __init__(
        self,
        timeframe: str = "H1",
        magic: int = 77001,
        deviation: int = 20,
        allow_live: bool = False,
        path: str = "",
    ) -> None:
        if timeframe not in TIMEFRAMES:
            raise GatewayError(f"unknown timeframe {timeframe!r}; use one of {list(TIMEFRAMES)}")
        self.timeframe = TIMEFRAMES[timeframe]
        self.magic = magic
        self.deviation = deviation
        self.allow_live = allow_live
        self.path = path
        self._resolved: dict[str, str] = {}
        self._connected = False

    # ---------------------------------------------------------------- connect
    def connect(self):
        kwargs = {"timeout": 120_000}
        if self.path:
            kwargs["path"] = self.path
        if not mt5.initialize(**kwargs):
            raise GatewayError(
                "Could not reach the MT5 terminal at "
                f"{self.path or 'auto-detected path'}. Is it open and logged in? "
                f"({mt5.last_error()})"
            )
        info = mt5.account_info()
        if info is None:
            mt5.shutdown()
            raise GatewayError("Terminal is open but no account is logged in.")

        if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL and not self.allow_live:
            mt5.shutdown()
            raise GatewayError(
                "LIVE account detected — refusing to run. "
                "Log into your Exness demo, or set ALLOW_LIVE=1 deliberately."
            )

        self._connected = True
        return info

    def shutdown(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False

    # ---------------------------------------------------------------- symbols
    def symbol(self, requested: str) -> str:
        """Resolve 'EURUSD' -> the exact broker name ('EURUSDm')."""
        key = requested.upper()
        cached = self._resolved.get(key)
        if cached and mt5.symbol_info(cached) is not None:
            return cached

        names = [s.name for s in (mt5.symbols_get() or [])]
        lookup = {n.lower(): n for n in names}

        for suffix in _SUFFIXES:
            candidate = lookup.get((key + suffix).lower())
            if candidate:
                mt5.symbol_select(candidate, True)
                self._resolved[key] = candidate
                return candidate

        # last resort: shortest name that starts with the base symbol
        starts = sorted((n for n in names if n.lower().startswith(key.lower())), key=len)
        if starts:
            mt5.symbol_select(starts[0], True)
            self._resolved[key] = starts[0]
            return starts[0]

        raise GatewayError(f"Symbol {requested} not found in Market Watch.")

    def symbol_info(self, symbol: str):
        info = mt5.symbol_info(self.symbol(symbol))
        if info is None:
            raise GatewayError(f"No symbol info for {symbol}")
        return info

    def value_per_price_per_lot(self, symbol: str) -> float:
        """Account-currency P/L for a 1.0 price move on 1.0 lot."""
        info = self.symbol_info(symbol)
        if not info.trade_tick_size:
            raise GatewayError(f"{symbol} has no tick size")
        return info.trade_tick_value / info.trade_tick_size

    def spread_points(self, symbol: str) -> float:
        return float(self.symbol_info(symbol).spread)

    # ------------------------------------------------------------------- data
    def candles(self, symbol: str, count: int = 600) -> pd.DataFrame:
        name = self.symbol(symbol)
        rates = mt5.copy_rates_from_pos(name, self.timeframe, 0, count)
        if rates is None or len(rates) == 0:
            raise GatewayError(f"No candles for {name}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def tick(self, symbol: str):
        tick = mt5.symbol_info_tick(self.symbol(symbol))
        if tick is None:
            raise GatewayError(f"No tick for {symbol}")
        return tick

    # -------------------------------------------------------------- account
    def account(self):
        info = mt5.account_info()
        if info is None:
            raise GatewayError("Lost connection to the terminal.")
        return info

    def positions(self, symbol: str | None = None) -> list:
        name = self.symbol(symbol) if symbol else None
        raw = mt5.positions_get(symbol=name) if name else mt5.positions_get()
        return [p for p in (raw or []) if p.magic == self.magic]

    def all_positions(self) -> list:
        return list(mt5.positions_get() or [])

    # ---------------------------------------------------------------- orders
    def _send(self, request: dict) -> OrderResult:
        last = None
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            result = mt5.order_send({**request, "type_filling": filling})
            if result is None:
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return OrderResult(
                    True, result.retcode, result.order, result.deal, result.price, result.comment
                )
            last = result
            if result.retcode != _INVALID_FILL:
                break
        if last is None:
            return OrderResult(False, -1, comment=str(mt5.last_error()))
        return OrderResult(False, last.retcode, comment=last.comment)

    def open_market(
        self, symbol: str, side: str, volume: float, sl: float, tp: float
    ) -> OrderResult:
        name = self.symbol(symbol)
        tick = self.tick(symbol)
        price = tick.ask if side == "buy" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": name,
            "volume": float(volume),
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": float(price),
            "sl": float(sl or 0.0),
            "tp": float(tp or 0.0),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "forex-bot",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        return self._send(request)

    def close_position(self, position) -> OrderResult:
        tick = self.tick(position.symbol)
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": float(position.volume),
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": position.ticket,
            "price": float(tick.bid if is_buy else tick.ask),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "forex-bot-close",
            "type_time": mt5.ORDER_TIME_GTC,
        }
        return self._send(request)

    def modify_sltp(self, position, sl: float, tp: float) -> OrderResult:
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": position.ticket,
            "sl": float(sl),
            "tp": float(tp),
            "magic": self.magic,
        }
        return self._send(request)

    def close_all(self) -> list[OrderResult]:
        return [self.close_position(p) for p in self.positions()]

    def deal_pnl(self, ticket: int, days: int = 3) -> tuple[float, float]:
        """Realised P/L and exit price for a closed position ticket."""
        from datetime import datetime, timedelta

        end = datetime.now()
        deals = mt5.history_deals_get(end - timedelta(days=days), end)
        pnl, exit_price = 0.0, 0.0
        for deal in deals or []:
            if deal.position_id != ticket:
                continue
            if deal.entry == mt5.DEAL_ENTRY_OUT:
                pnl += deal.profit + deal.commission + deal.swap
                exit_price = deal.price
        return pnl, exit_price

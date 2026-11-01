"""Offline self-test — no MT5 needed. Run: python selftest.py

Builds synthetic candles, exercises the strategy, the backtester and the ledger,
and imports every module. If this passes, the code itself is healthy and any
remaining problem is the terminal connection.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from config import settings
from strategy import analyze, prepare


def synthetic_candles(n: int = 800, start: float = 1.1000) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    drift = np.linspace(0, 0.05, n)
    noise = rng.normal(0, 0.0009, n).cumsum()
    close = start + drift + noise
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    span = np.abs(rng.normal(0, 0.0006, n)) + 0.0002
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    times = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"time": times, "open": open_, "high": high, "low": low, "close": close}
    )


def main() -> int:
    df = synthetic_candles()

    prepared = prepare(df)
    assert "ema_fast" in prepared and "adx" in prepared, "indicators missing"
    assert prepared["atr"].iloc[-1] > 0, "ATR is zero"

    result = analyze(df)
    print(f"strategy      : side={result.side!r} atr={result.atr:.5f} adx={result.adx:.1f} ({result.reason})")

    from backtest import report, run_backtest

    bt = run_backtest(df, value_per_unit=1000.0, settings=settings, symbol="EURUSD", spread_price=0.00012)
    print(report(bt))
    assert bt.trades >= 0 and len(bt.curve) > 0, "backtest produced no curve"

    from ledger import Ledger

    with tempfile.TemporaryDirectory() as tmp:
        led = Ledger(Path(tmp) / "test.db")
        led.snapshot(10_000.0)
        trade_id = led.open_trade(111, "EURUSD", "buy", 0.10, 1.1000, 1.0950, 1.1100, "demo")
        led.close_trade(111, 1.1100, 50.0, "target")
        assert trade_id == 1
        assert led.pnl_since("2000-01-01T00:00:00") == 50.0
        assert len(led.recent_trades()) == 1
        assert led.open_trades() == []
        assert led.equity_series()[0]["value"] == 10_000.0
        led.close()
    print("ledger        : OK")

    import api  # noqa: F401  (constructs app; must not connect to MT5 at import)
    import engine  # noqa: F401
    import risk  # noqa: F401
    import mt5_gateway  # noqa: F401
    print("imports       : OK")

    print("\nSELF-TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

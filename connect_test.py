"""First contact test. Run this before anything else.

    python connect_test.py

It connects to the running MT5 terminal, prints your account, resolves every
symbol in SYMBOLS to its exact broker name, and pulls a few candles.
It places NO orders.
"""
from __future__ import annotations

import sys

from config import settings
from mt5_gateway import GatewayError, MT5Gateway


def main() -> int:
    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        settings.allow_live, settings.mt5_path,
    )

    try:
        info = gw.connect()
    except GatewayError as exc:
        print(f"[FAIL] {exc}")
        return 1

    kind = "DEMO" if info.trade_mode != 2 else "LIVE"
    print("=" * 58)
    print(f" Connected   : {kind}")
    print(f" Login       : {info.login}")
    print(f" Server      : {info.server}")
    print(f" Currency    : {info.currency}")
    print(f" Balance     : {info.balance:,.2f}")
    print(f" Equity      : {info.equity:,.2f}")
    print(f" Leverage    : 1:{info.leverage}")
    print("=" * 58)

    failures = 0
    for base in settings.symbols:
        try:
            name = gw.symbol(base)
            info_s = gw.symbol_info(base)
            tick = gw.tick(base)
            df = gw.candles(base, 5)
            last = df.iloc[-1]
            print(
                f" {base:<8} -> {name:<12} spread={info_s.spread:>4} pts  "
                f"bid={tick.bid:<10.5f} ask={tick.ask:<10.5f} "
                f"bars={len(df)} last_close={last['close']:.5f}"
            )
        except Exception as exc:
            failures += 1
            print(f" {base:<8} -> [FAIL] {exc}")

    gw.shutdown()
    print("=" * 58)
    if failures:
        print(f"{failures} symbol(s) failed. Add them to Market Watch in MT5.")
        return 2
    print("Everything looks good. Start the dashboard with:")
    print("  python -m uvicorn api:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())

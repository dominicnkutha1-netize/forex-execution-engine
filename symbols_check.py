"""Inspect what the broker actually offers for our symbols.

    python symbols_check.py

Shows, per symbol: exact name, visibility, trade mode, session state,
spread, live tick and tick age. Read-only.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

from config import settings
from mt5_gateway import MT5Gateway

TRADE_MODES = {
    0: "DISABLED",
    1: "LONG ONLY",
    2: "SHORT ONLY",
    3: "CLOSE ONLY",
    4: "FULL",
}
SESSION = {0: "closed", 1: "open", 2: "suspended", 3: "resume", 4: "reset", 5: "reconnect"}


def main() -> int:
    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        settings.allow_live, settings.mt5_path,
    )
    gw.connect()

    all_names = [s.name for s in (mt5.symbols_get() or [])]
    print(f"total symbols on this account: {len(all_names)}")

    for base in settings.symbols:
        matches = [n for n in all_names if n.upper().startswith(base.upper())]
        if not matches:
            print(f"\n{base}: NOT OFFERED by this broker")
            continue

        name = gw.symbol(base)
        mt5.symbol_select(name, True)
        time.sleep(1.5)  # give the terminal a moment to push the first tick

        info = mt5.symbol_info(name)
        tick = mt5.symbol_info_tick(name)
        age = None
        if tick and tick.time:
            age = (datetime.now(timezone.utc) - datetime.fromtimestamp(tick.time, timezone.utc)).seconds

        print(f"\n{base} -> {name}   (candidates: {', '.join(matches[:6])})")
        print(f"  visible      : {info.visible}")
        print(f"  trade mode   : {TRADE_MODES.get(info.trade_mode, info.trade_mode)}")
        print(f"  session      : {SESSION.get(info.session_deals, info.session_deals)}")
        print(f"  spread       : {info.spread} points (point={info.point}, digits={info.digits})")
        print(f"  volume min   : {info.volume_min}  step={info.volume_step}")
        print(f"  bid / ask    : {tick.bid} / {tick.ask}")
        print(f"  last tick    : {age}s ago" if age is not None else "  last tick    : n/a")

    gw.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

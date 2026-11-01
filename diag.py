"""Diagnose the terminal <-> broker link. Run: python diag.py"""
from __future__ import annotations

import time
from pathlib import Path

import MetaTrader5 as mt5

from config import settings


def main() -> int:
    kwargs = {"timeout": 120_000}
    if settings.mt5_path:
        kwargs["path"] = settings.mt5_path
    if not mt5.initialize(**kwargs):
        print("initialize failed:", mt5.last_error())
        return 1

    ti = mt5.terminal_info()
    ai = mt5.account_info()

    print("--- terminal ---")
    for field in ("connected", "build", "maxbars", "trade_allowed", "dlls_allowed",
                  "community_account", "ping_last", "path", "data_path"):
        print(f"  {field:<18}: {getattr(ti, field, '?')}")

    print("--- account ---")
    for field in ("login", "server", "trade_mode", "balance", "equity", "margin_free"):
        print(f"  {field:<18}: {getattr(ai, field, '?')}")

    print("--- ticks ---")
    for base in settings.symbols:
        info = mt5.symbol_info(base)
        if info is None:
            print(f"  {base:<8}: missing")
            continue
        mt5.symbol_select(base, True)
        time.sleep(0.4)
        tick = mt5.symbol_info_tick(base)
        age = int(time.time() - tick.time) if tick and tick.time else -1
        print(f"  {base:<8}: bid={tick.bid if tick else '?'} session_deals={info.session_deals} tick_age={age}s")

    mt5.shutdown()

    print("--- recent log lines mentioning the link ---")
    base_dir = Path.home() / "AppData/Roaming/MetaQuotes/Terminal"
    logs = sorted(base_dir.glob("*/logs/*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if logs:
        text = logs[0].read_text(encoding="utf-16", errors="replace")
        keys = ("login", "authoriz", "network", "connect", "ping", "failed", "server")
        for line in text.splitlines()[-120:]:
            if any(k in line.lower() for k in keys):
                print(" ", line.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

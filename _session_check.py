"""Does the edge depend on the time of day? (backtest bars are BROKER time, GMT+3)

Compares the final validated params with no session filter vs several UTC windows,
so the live engine's session filter is consistent with what was tested.

    python _session_check.py
"""
from __future__ import annotations

import pandas as pd

from backtest import simulate
from config import settings
from mt5_gateway import MT5Gateway
from strategy import prepare

BROKER_UTC_OFFSET = 3  # MetaQuotes-Demo is GMT+3
WINDOWS = [None, (7, 17), (7, 21), (12, 21), (13, 22)]


def main() -> int:
    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        allow_live=False, path=settings.mt5_path,
    )
    gw.connect()

    rows = []
    for base in settings.symbols:
        history = gw.candles(base, 20000)
        info = gw.symbol_info(base)
        spread = info.spread * info.point
        value = gw.value_per_price_per_lot(base)
        risk = settings.risk_for(base)
        p = settings.params_for(base)

        raw = prepare(
            history,
            lookback=p["lookback"], exit_lookback=p["exit_lookback"], adx_min=p["adx_min"],
        ).dropna(subset=["atr", "adx", "dc_high", "dc_low"]).reset_index(drop=True)

        utc_hour = (pd.to_datetime(raw["time"]).dt.hour - BROKER_UTC_OFFSET) % 24
        raw = raw.copy()
        raw["time"] = pd.to_datetime(raw["time"]).dt.tz_convert("UTC") - pd.Timedelta(
            hours=BROKER_UTC_OFFSET
        )

        for window in WINDOWS:
            r = simulate(
                raw.assign(_utc=utc_hour), value, base, spread, risk,
                min_stop_atr=p["min_stop_atr"],
                reward_risk=p["reward_risk"],
                trail_atr=p["trail_atr"],
                record_curve=False,
                entry_hours=window,
            )
            rows.append(
                {
                    "symbol": base,
                    "utc_window": "24h" if window is None else f"{window[0]:02d}-{window[1]:02d}",
                    "trades": r.trades,
                    "pf": round(min(r.profit_factor, 99), 3),
                    "ret%": round(r.pnl / 10_000 * 100, 2),
                    "dd%": round(r.max_drawdown_pct, 2),
                    "avgR": round(r.avg_r, 3),
                }
            )

    gw.shutdown()
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    df.to_csv("reports/session_check.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

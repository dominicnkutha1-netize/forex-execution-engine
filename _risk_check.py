"""How does risk-per-trade change return vs drawdown on the validated gold setup?

Session filter (07-17 UTC) is applied because that is when the bot really trades.
    python _risk_check.py
"""
from __future__ import annotations

import pandas as pd

from backtest import simulate
from config import settings
from mt5_gateway import MT5Gateway
from strategy import prepare

BROKER_UTC_OFFSET = 3
RISK_LEVELS = [0.0035, 0.005, 0.0075, 0.010]
WINDOW = (7, 17)


def main() -> int:
    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        allow_live=False, path=settings.mt5_path,
    )
    gw.connect()

    base = "XAUUSD"
    history = gw.candles(base, 20000)
    info = gw.symbol_info(base)
    spread = info.spread * info.point
    value = gw.value_per_price_per_lot(base)
    p = settings.params_for(base)

    raw = prepare(
        history,
        lookback=p["lookback"], exit_lookback=p["exit_lookback"], adx_min=p["adx_min"],
    ).dropna(subset=["atr", "adx", "dc_high", "dc_low"]).reset_index(drop=True)
    raw["time"] = pd.to_datetime(raw["time"]).dt.tz_convert("UTC") - pd.Timedelta(
        hours=BROKER_UTC_OFFSET
    )
    gw.shutdown()

    rows = []
    for risk in RISK_LEVELS:
        r = simulate(
            raw, value, base, spread, risk,
            min_stop_atr=p["min_stop_atr"],
            reward_risk=p["reward_risk"],
            trail_atr=p["trail_atr"],
            record_curve=False,
            entry_hours=WINDOW,
        )
        years = 3.0
        rows.append(
            {
                "risk%": risk * 100,
                "trades": r.trades,
                "pf": round(min(r.profit_factor, 99), 3),
                "ret%": round(r.pnl / 10_000 * 100, 2),
                "dd%": round(r.max_drawdown_pct, 2),
                "ret/dd": round((r.pnl / 10_000 * 100) / max(r.max_drawdown_pct, 0.01), 2),
                "per_year%": round(r.pnl / 10_000 * 100 / years, 2),
                "end_equity": round(r.equity_end, 2),
            }
        )

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    df.to_csv("reports/risk_check.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

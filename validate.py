"""Final honesty check: run the *chosen* per-symbol params on real history.

    python validate.py

For every symbol in .env it reports:
  * full sample  - all ~3 years, train + test together
  * holdout      - the last 30% only (the part the sweep never ranked on)

A config is only worth demo-trading if BOTH are positive with a sane drawdown.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from backtest import report, run_backtest
from config import settings
from mt5_gateway import MT5Gateway
from strategy import prepare
from backtest import simulate


def main() -> int:
    symbols = [s.upper() for s in sys.argv[1:]] or list(settings.symbols)

    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        allow_live=False, path=settings.mt5_path,
    )
    gw.connect()

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    rows = []

    for base in symbols:
        history = gw.candles(base, 20000)
        info = gw.symbol_info(base)
        spread = info.spread * info.point
        value = gw.value_per_price_per_lot(base)
        risk = settings.risk_for(base)
        params = settings.params_for(base)

        full = run_backtest(history, value, settings, base, spread, risk, params)

        split = int(len(history) * 0.7)
        hold_raw = history.iloc[split:].reset_index(drop=True)
        hold = simulate(
            prepare(hold_raw, **{k: params[k] for k in
                                 ("lookback", "exit_lookback", "adx_min")}
                    ).dropna(subset=["atr", "adx", "dc_high", "dc_low"]).reset_index(drop=True),
            value, base, spread, risk,
            min_stop_atr=params["min_stop_atr"],
            reward_risk=params["reward_risk"],
            trail_atr=params["trail_atr"],
            record_curve=False,
        )

        print(f"\n{'=' * 62}\n{base}   params={params}\n{'=' * 62}")
        print("[full sample]")
        print(report(full))
        print("[holdout: last 30%]")
        print(report(hold))

        rows.append(
            {
                "symbol": base,
                "lookback": params["lookback"],
                "adx_min": params["adx_min"],
                "min_stop_atr": params["min_stop_atr"],
                "reward_risk": params["reward_risk"],
                "trail_atr": params["trail_atr"],
                "full_trades": full.trades,
                "full_pf": round(min(full.profit_factor, 99), 3),
                "full_ret": round(full.pnl / 10_000 * 100, 2),
                "full_dd": round(full.max_drawdown_pct, 2),
                "hold_trades": hold.trades,
                "hold_pf": round(min(hold.profit_factor, 99), 3),
                "hold_ret": round(hold.pnl / 10_000 * 100, 2),
                "hold_dd": round(hold.max_drawdown_pct, 2),
            }
        )

        pd.DataFrame(full.curve).to_csv(out_dir / f"final_{base}.csv", index=False)

    gw.shutdown()

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "final_summary.csv", index=False)
    print(f"\n{'=' * 62}")
    print(df.to_string(index=False))
    print("\nwritten: reports/final_summary.csv, reports/final_<SYMBOL>.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

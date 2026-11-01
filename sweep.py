"""Parameter sweep with a 70/30 train-test split.

    python sweep.py                # all symbols
    python sweep.py XAUUSD USDCAD  # specific symbols

Rules of the game:
  * indicators are computed separately on train and test (no leakage)
  * a config must make >= 40 trades on train to be considered
  * configs are RANKED ON TRAIN ONLY, then the top few are shown on test
  * a symbol is only kept if its best-train config still has PF >= 1.05 on test

Anything that only works on train is curve-fitting. This tries to catch that.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pandas as pd

from backtest import report, simulate
from config import settings
from mt5_gateway import MT5Gateway
from strategy import prepare

GRID_LOOKBACK = [20, 30]
GRID_ADX = [18.0, 22.0]
GRID_STOP = [1.5, 2.0]
GRID_RR = [2.0, 3.0]
GRID_TRAIL = [2.5, 3.0]

MIN_TRAIN_TRADES = 40
MIN_TEST_TRADES = 15
MIN_TEST_PF = 1.05


def _score(r) -> float:
    if r.trades < MIN_TRAIN_TRADES:
        return -1.0
    pf = 10.0 if r.profit_factor == float("inf") else r.profit_factor
    return pf


def main() -> int:
    symbols = [s.upper() for s in sys.argv[1:]] or list(settings.symbols)

    gw = MT5Gateway(
        settings.timeframe, settings.magic, settings.deviation,
        allow_live=False, path=settings.mt5_path,
    )
    gw.connect()

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    sel_path = out_dir / "selection.json"
    # keep earlier results when re-running a subset (e.g. "sweep.py XAUUSD")
    selection: dict[str, dict | None] = {}
    if sel_path.exists():
        try:
            selection = json.loads(sel_path.read_text())
        except (ValueError, OSError):
            selection = {}

    for base in symbols:
        history = gw.candles(base, 20000)
        info = gw.symbol_info(base)
        spread = info.spread * info.point
        value = gw.value_per_price_per_lot(base)
        risk = settings.risk_for(base)

        split = int(len(history) * 0.7)
        train_raw = history.iloc[:split].reset_index(drop=True)
        test_raw = history.iloc[split:].reset_index(drop=True)

        rows = []
        for lookback, adx_min in itertools.product(GRID_LOOKBACK, GRID_ADX):
            train = prepare(train_raw, lookback=lookback, adx_min=adx_min).dropna(
                subset=["atr", "adx", "dc_high", "dc_low"]
            )
            test = prepare(test_raw, lookback=lookback, adx_min=adx_min).dropna(
                subset=["atr", "adx", "dc_high", "dc_low"]
            )
            if len(train) < 200 or len(test) < 100:
                continue

            for stop, rr, trail in itertools.product(GRID_STOP, GRID_RR, GRID_TRAIL):
                tr = simulate(
                    train, value, base, spread, risk,
                    min_stop_atr=stop, reward_risk=rr, trail_atr=trail,
                    record_curve=False,
                )
                if tr.trades < MIN_TRAIN_TRADES:
                    continue
                te = simulate(
                    test, value, base, spread, risk,
                    min_stop_atr=stop, reward_risk=rr, trail_atr=trail,
                    record_curve=False,
                )
                rows.append(
                    {
                        "symbol": base,
                        "lookback": lookback,
                        "adx_min": adx_min,
                        "min_stop_atr": stop,
                        "reward_risk": rr,
                        "trail_atr": trail,
                        "train_trades": tr.trades,
                        "train_pf": round(min(tr.profit_factor, 10), 3),
                        "train_ret": round(tr.pnl / 10_000 * 100, 2),
                        "train_dd": round(tr.max_drawdown_pct, 2),
                        "test_trades": te.trades,
                        "test_pf": round(min(te.profit_factor, 10), 3),
                        "test_ret": round(te.pnl / 10_000 * 100, 2),
                        "test_dd": round(te.max_drawdown_pct, 2),
                        "_train": tr,
                        "_test": te,
                    }
                )

        if not rows:
            print(f"\n{base}: no config made enough trades")
            selection[base] = None
            continue

        df = pd.DataFrame(rows)
        df_sorted = df.sort_values("train_pf", ascending=False).reset_index(drop=True)

        print(f"\n{'=' * 78}")
        print(f"{base}   train={len(train_raw)} bars   test={len(test_raw)} bars   spread={spread:.5f}")
        print(f"{'lb':>3} {'adx':>4} {'stop':>5} {'rr':>4} {'trail':>6} | "
              f"{'n':>4} {'trainPF':>8} {'train%':>7} | {'n':>4} {'testPF':>7} {'test%':>7}")
        for _, r in df_sorted.head(8).iterrows():
            print(f"{r['lookback']:>3} {r['adx_min']:>4.0f} {r['min_stop_atr']:>5.1f} "
                  f"{r['reward_risk']:>4.1f} {r['trail_atr']:>6.1f} | "
                  f"{r['train_trades']:>4} {r['train_pf']:>8.3f} {r['train_ret']:>+7.2f} | "
                  f"{r['test_trades']:>4} {r['test_pf']:>7.3f} {r['test_ret']:>+7.2f}")

        best = df_sorted.iloc[0]
        keep = (
            best["test_pf"] >= MIN_TEST_PF
            and best["test_trades"] >= MIN_TEST_TRADES
            and best["test_ret"] > 0
        )
        verdict = "KEEP" if keep else "DROP"
        print(f"  best-by-train -> test PF {best['test_pf']:.3f}, "
              f"{best['test_ret']:+.2f}% on {best['test_trades']} trades  =>  {verdict}")

        if keep:
            selection[base] = {
                "lookback": int(best["lookback"]),
                "adx_min": float(best["adx_min"]),
                "min_stop_atr": float(best["min_stop_atr"]),
                "reward_risk": float(best["reward_risk"]),
                "trail_atr": float(best["trail_atr"]),
                "train_pf": float(best["train_pf"]),
                "test_pf": float(best["test_pf"]),
            }
            print(report(best["_test"]))
        else:
            selection[base] = None

        df.drop(columns=["_train", "_test"]).to_csv(out_dir / f"sweep_{base}.csv", index=False)
        # save after every symbol so a crash on this slow laptop never loses earlier work
        (out_dir / "selection.json").write_text(json.dumps(selection, indent=2))

    (out_dir / "selection.json").write_text(json.dumps(selection, indent=2))
    gw.shutdown()

    print(f"\n{'=' * 78}")
    print("SURVIVORS:", ", ".join(k for k, v in selection.items() if v) or "none")
    print("selection written to reports/selection.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

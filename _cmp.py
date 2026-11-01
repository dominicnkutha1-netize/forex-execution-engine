import pandas as pd

for sym in ("XAUUSD", "USDCAD"):
    d = pd.read_csv(f"reports/sweep_{sym}.csv")
    q = d[(d.min_stop_atr == 2.0) & (d.reward_risk == 3.0) & (d.adx_min == 22.0)]
    print(f"--- {sym}  stop=2.0 rr=3.0 adx=22 ---")
    print(q[["lookback", "trail_atr", "train_trades", "train_pf", "test_trades", "test_pf", "test_ret", "test_dd"]].to_string(index=False))

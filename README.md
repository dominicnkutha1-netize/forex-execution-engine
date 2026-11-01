# Forex Bot (Exness / MetaTrader 5)

A small, deliberately boring forex + gold bot that runs against your **Exness demo**
through the MetaTrader 5 terminal, with a live dashboard.

It is not a money printer. It is a risk-managed execution engine you can watch,
backtest, pause and kill. Read the safety section before you do anything.

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.13%20%7C%203.14-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-dashboard-009688?logo=fastapi&logoColor=white">
  <img alt="MetaTrader 5" src="https://img.shields.io/badge/MetaTrader-5-0A66C2">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-lightgrey">
  <img alt="Default mode" src="https://img.shields.io/badge/default-read--only-brightgreen">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
</p>

**Jump to:** [Setup](#1-prerequisites) · [Dashboard](#4-run-the-dashboard-safe-mode) ·
[Backtest](#5-backtest-before-trading) · [Validated results](#5b-the-validated-setup-what-this-bot-actually-trades) ·
[Demo trading](#6-demo-auto-trading) · [Safety rails](#safety-rails-already-built-in) ·
[Limitations](#known-limitations-v1) · [Roadmap](#roadmap)

---

## What is in the box

| File | Job |
|---|---|
| `config.py` | all settings, loaded from `.env` |
| `mt5_gateway.py` | the only file that talks to MT5 (prices, candles, orders) |
| `risk.py` | session filter, daily loss breaker, spread guard, lot sizing |
| `strategy.py` | Donchian breakout + EMA trend + ADX filter |
| `backtest.py` | pessimistic backtester (spread, next-bar fills, stop-first) |
| `engine.py` | the loop: reconcile → risk → manage → trade |
| `ledger.py` | SQLite record of every trade + equity snapshots |
| `api.py` | FastAPI server + `/api/state` for the dashboard |
| `web/index.html` | the dashboard (dark, TradingView charts) |

---

## 1. Prerequisites

- Windows (MT5's Python API is Windows-only)
- **Python 3.13 or 3.14** — https://www.python.org/downloads/ → tick **"Add python.exe to PATH"**
  - Python **3.15 does not work yet**: MetaTrader5 has no wheel for it (verified 2026-10).
  - This project's `.venv` was built with Python 3.14 at
    `C:\Users\MR Nkutha\AppData\Local\Programs\Python\Python314\python.exe`
- **MetaTrader 5** from Exness (their installer, not the generic MetaQuotes one)
- An **Exness demo account**, logged into that terminal

Verify Python:

```cmd
python --version
```

## 2. Get the Exness demo running

1. Open MT5 → log into your Exness **demo**.
2. Bottom-right should show a connection and a balance.
3. Confirm it says **Demo** in the account bar.
4. Open **Market Watch** and make sure `EURUSD` and `XAUUSD` are visible
   (right-click → Show All, or type the symbol).
5. Note the exact symbol spelling. Exness often uses a suffix (`EURUSDm`, `XAUUSDm`).
   **You don't need to hard-code it** — the bot resolves suffixes automatically.

## 3. Install

```cmd
cd forex-bot-mt5
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` if you want different symbols or risk. Defaults are sane and
**read-only** (`AUTO_TRADE=0`).

> Your MT5 password never goes in this project. The bot uses the already-logged-in
> terminal, so there are no credentials in the code at all.

## 4. Run the dashboard (safe mode)

MT5 must be open and logged in. Then:

```cmd
python -m uvicorn api:app --port 8000
```

Open <http://127.0.0.1:8000>

You should see balance, equity, and "DEMO · your-server". No orders will be placed
while `AUTO_TRADE=0`. This is the stage where we verify the plumbing.

Sanity check the symbol mapping:

```cmd
curl http://127.0.0.1:8000/api/symbols
```

It returns the exact broker names, e.g. `["EURUSDm","XAUUSDm"]`.

## 5. Backtest before trading

```cmd
python backtest.py EURUSD
python backtest.py XAUUSD
python backtest.py GBPUSD
```

This pulls ~20,000 bars from your broker and prints trades, win rate, return,
max drawdown and profit factor, plus a CSV equity curve in `reports/`.

Judge it honestly:

- profit factor below ~1.2 → not worth trading
- max drawdown above ~10% → too spicy for the return
- fewer than ~30 trades → statistically meaningless
- **any** result that looks too good is usually a data or cost assumption problem

## 5b. The validated setup (what this bot actually trades)

`backtest.py` alone is in-sample and lies to you. The real pipeline is:

```cmd
python sweep.py           :: 70/30 train / test, ranks on train only
python validate.py        :: final params on full history + last 30% holdout
```

`sweep.py` writes one CSV per symbol plus `reports/selection.json`.
A symbol only survives if the config that wins on *train* still has
PF >= 1.05 and a positive return on the *unseen* 30%.

Current survivors on ~3 years of H1 MetaQuotes data (spread included):

| Symbol | Trades | Full return | Full DD | Full PF | Holdout PF | Holdout return |
|---|---:|---:|---:|---:|---:|---:|
| XAUUSD | 379 | +28.11% | 5.49% | 1.41 | 1.51 | +10.08% |
| USDCAD | 528 | +13.28% | 8.26% | 1.09 | 1.12 | +5.84% |

EURUSD, GBPUSD, USDJPY and AUDUSD were **dropped** — no parameter set
survived the holdout on them. They are disabled in `SYMBOLS` on purpose.

Per-symbol parameters live in `params.json` (not in code), so re-tuning never
means editing Python:

```json
{
  "_default": { "lookback": 20, "exit_lookback": 10, "adx_min": 22.0,
                "min_stop_atr": 2.0, "reward_risk": 3.0, "trail_atr": 2.5 },
  "USDCAD":   { "lookback": 30, "adx_min": 18.0,
                "min_stop_atr": 1.5, "reward_risk": 2.0, "trail_atr": 3.0 }
}
```

Any symbol not listed inherits `_default`. Delete the file and everything
falls back to `.env`. **Re-run `sweep.py` + `validate.py` before changing it.**

## 6. Demo auto-trading

When the dashboard is stable and the backtest isn't embarrassing:

```env
AUTO_TRADE=1
```

Restart the server. The bot will only act during London/NY hours (07:00–17:00 UTC),
skip weekends, skip Friday evening, and stop for the day after a 2% loss.

Watch it for **weeks**. Log every weird behaviour. Only then discuss live money.

## 7. Live money (much later)

Two separate locks must both be opened:

1. `.env` → `ALLOW_LIVE=1`
2. Log the terminal into a **real** account

Until then, `mt5_gateway.connect()` refuses to run on a real account:

```python
if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_REAL and not self.allow_live:
    raise GatewayError("LIVE account detected — refusing to run.")
```

Start with the smallest size the broker allows. Cent/micro account if available.

---

## Safety rails already built in

- **Demo-only guard** — hard refusal on real accounts unless explicitly unlocked
- **Read-only default** — `AUTO_TRADE=0`
- **Daily loss breaker** — flattens and pauses for the day at `MAX_DAILY_LOSS`
- **Max open trades** — default 2
- **One position per symbol**
- **Spread guard** — no entries when the spread blows out
- **Session filter** — London/NY only, no weekends, no Friday evening
- **ATR stops** — volatility-sized, with a trailing stop
- **Kill switch** — dashboard button: close everything, pause engine
- **Gold gets less risk** — 0.35% vs 0.5% per trade

## Known limitations (v1)

- No economic-calendar / news filter yet
- No correlation cap yet (EURUSD + GBPUSD + XAUUSD can all be one USD bet)
- No Telegram/email alerts yet
- No Grok layer yet — `XAI_API_KEY` is in `.env` but unused
- Backtest is single-position, single-symbol, no swap/financing modelling
- Ledger reconciliation relies on MT5 deal history for the last 3 days

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not reach the MT5 terminal` | MT5 must be **open and logged in** before starting the server |
| `Symbol ... not found` | Add it to Market Watch, or fix the base name in `SYMBOLS` |
| `order rejected [10027]` | Auto-trading disabled in the terminal: Tools → Options → Expert Advisors → allow algo trading |
| `order rejected [10030]` | Broker rejects the filling mode; the gateway retries IOC/FOK/RETURN automatically |
| `order rejected [10018]` | Market closed — expected on weekends |
| Dashboard offline | Is uvicorn still running? Check the console |

## Roadmap

1. News/calendar veto (economic calendar API)
2. Correlation cap across open positions
3. Telegram alerts (trade opened/closed, breaker tripped)
4. Grok layer: pre-trade news veto + plain-English trade commentary
5. Multi-strategy support with per-strategy capital allocation
6. Walk-forward optimisation instead of one-shot backtests

---

Nothing here is financial advice. Trading leveraged FX and gold can lose more than
you deposit. Run it on demo until the numbers are boring.

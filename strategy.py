"""Strategy: Donchian breakout + trend + volatility filter.

The contract is deliberately small (`analyze(df)`), so a different strategy
can be dropped in later without touching risk, execution or the dashboard.

Rules
  long   : close breaks the 20-bar high, EMA20 > EMA50, ADX > 22
  short  : close breaks the 20-bar low,  EMA20 < EMA50, ADX > 22
  exit   : close crosses back through the 10-bar opposite extreme
  stops  : ATR * MIN_STOP_ATR, target = REWARD_RISK * risk
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FAST, SLOW, LOOKBACK, EXIT_LOOKBACK, ATR_PERIOD, ADX_PERIOD = 20, 50, 20, 10, 14, 14
ADX_MIN = 22.0


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up, down = high.diff(), -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def prepare(
    df: pd.DataFrame,
    fast: int = FAST,
    slow: int = SLOW,
    lookback: int = LOOKBACK,
    exit_lookback: int = EXIT_LOOKBACK,
    adx_min: float = ADX_MIN,
) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], fast)
    out["ema_slow"] = ema(out["close"], slow)
    out["atr"] = atr(out)
    out["adx"] = adx(out)
    out["dc_high"] = out["high"].rolling(lookback).max().shift(1)
    out["dc_low"] = out["low"].rolling(lookback).min().shift(1)
    out["exit_high"] = out["high"].rolling(exit_lookback).max().shift(1)
    out["exit_low"] = out["low"].rolling(exit_lookback).min().shift(1)
    out["long_signal"] = (
        (out["close"] > out["dc_high"])
        & (out["ema_fast"] > out["ema_slow"])
        & (out["adx"] >= adx_min)
    )
    out["short_signal"] = (
        (out["close"] < out["dc_low"])
        & (out["ema_fast"] < out["ema_slow"])
        & (out["adx"] >= adx_min)
    )
    out["exit_long"] = out["close"] < out["exit_low"]
    out["exit_short"] = out["close"] > out["exit_high"]
    return out


@dataclass
class Analysis:
    side: str | None
    atr: float
    exit_long: bool
    exit_short: bool
    adx: float
    reason: str = ""
    close: float = 0.0
    dc_high: float = 0.0
    dc_low: float = 0.0
    trend_up: bool = False


def analyze(
    df: pd.DataFrame,
    min_bars: int = 60,
    lookback: int = LOOKBACK,
    exit_lookback: int = EXIT_LOOKBACK,
    adx_min: float = ADX_MIN,
) -> Analysis:
    """Evaluate the most recent *closed* bar."""
    empty = Analysis(None, 0.0, False, False, 0.0, "not enough history")
    if len(df) < min_bars:
        return empty

    bar = prepare(
        df, lookback=lookback, exit_lookback=exit_lookback, adx_min=adx_min
    ).iloc[-1]
    if pd.isna(bar["atr"]) or pd.isna(bar["adx"]):
        return empty

    atr_value = float(bar["atr"])
    common = {
        "exit_long": bool(bar["exit_long"]),
        "exit_short": bool(bar["exit_short"]),
        "close": float(bar["close"]),
        "dc_high": float(bar["dc_high"]),
        "dc_low": float(bar["dc_low"]),
        "trend_up": bool(bar["ema_fast"] > bar["ema_slow"]),
    }
    if bar["adx"] < adx_min:
        return Analysis(None, atr_value, adx=float(bar["adx"]), reason="choppy (ADX)", **common)

    if bool(bar["long_signal"]):
        return Analysis("buy", atr_value, adx=float(bar["adx"]), reason="breakout long", **common)
    if bool(bar["short_signal"]):
        return Analysis("sell", atr_value, adx=float(bar["adx"]), reason="breakout short", **common)

    return Analysis(None, atr_value, adx=float(bar["adx"]), reason="no setup", **common)

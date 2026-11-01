"""Central configuration. Every value can be overridden in .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from json import JSONDecodeError, load as json_load

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # execution
    mode: str = os.getenv("MODE", "demo").strip().lower()
    auto_trade: bool = env_bool("AUTO_TRADE", False)
    allow_live: bool = env_bool("ALLOW_LIVE", False)
    # optional: full path to terminal64.exe if the auto-discovery is flaky
    mt5_path: str = os.getenv("MT5_PATH", "").strip()
    symbols: tuple[str, ...] = tuple(
        s.strip().upper()
        for s in os.getenv("SYMBOLS", "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,XAUUSD").split(",")
        if s.strip()
    )
    timeframe: str = os.getenv("TIMEFRAME", "H1").strip().upper()
    loop_seconds: int = _int("LOOP_SECONDS", 15)
    magic: int = _int("MAGIC", 77001)
    deviation: int = _int("DEVIATION", 20)

    # risk
    risk_per_trade: float = _float("RISK_PER_TRADE", 0.005)
    max_gold_risk: float = _float("MAX_GOLD_RISK", 0.0035)
    max_daily_loss: float = _float("MAX_DAILY_LOSS", 0.02)
    max_open_trades: int = _int("MAX_OPEN_TRADES", 2)
    min_stop_atr: float = _float("MIN_STOP_ATR", 1.5)
    reward_risk: float = _float("REWARD_RISK", 2.0)
    trail_atr: float = _float("TRAIL_ATR", 2.5)
    # strategy shape (validated by sweep.py)
    lookback: int = _int("LOOKBACK", 20)
    exit_lookback: int = _int("EXIT_LOOKBACK", 10)
    adx_min: float = _float("ADX_MIN", 22.0)
    params_path: str = os.getenv("PARAMS_PATH", str(ROOT / "params.json"))
    max_spread_points: float = _float("MAX_SPREAD_POINTS", 30)
    max_gold_spread_points: float = _float("MAX_GOLD_SPREAD_POINTS", 60)

    # session, UTC hours
    session_start_utc: int = _int("SESSION_START_UTC", 7)
    session_end_utc: int = _int("SESSION_END_UTC", 17)
    skip_friday_after_utc: int = _int("SKIP_FRIDAY_AFTER_UTC", 18)

    # storage
    db_path: str = os.getenv("DB_PATH", str(ROOT / "bot.db"))

    def risk_for(self, symbol: str) -> float:
        """Gold is slower and gets a smaller slice of the account."""
        return self.max_gold_risk if symbol.upper().startswith("XAU") else self.risk_per_trade

    def max_spread_for(self, symbol: str) -> float:
        """Gold is quoted in cents, so its point spread is naturally wider."""
        return (
            self.max_gold_spread_points
            if symbol.upper().startswith("XAU")
            else self.max_spread_points
        )

    def params_for(self, symbol: str) -> dict:
        """Per-symbol strategy parameters from params.json, falling back to .env.

        params.json looks like:
            {"_default": {...}, "XAUUSD": {...}, "USDCAD": {...}}
        Unknown keys are ignored so a stale file can never crash the engine.
        """
        base = {
            "lookback": self.lookback,
            "exit_lookback": self.exit_lookback,
            "adx_min": self.adx_min,
            "min_stop_atr": self.min_stop_atr,
            "reward_risk": self.reward_risk,
            "trail_atr": self.trail_atr,
        }
        table = _params_table(self.params_path)
        for key in ("_default", symbol.upper()):
            override = table.get(key) or {}
            for name in base:
                if name in override:
                    try:
                        base[name] = type(base[name])(override[name])
                    except (TypeError, ValueError):
                        pass
        return base


@lru_cache(maxsize=1)
def _params_table(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json_load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, JSONDecodeError):
        return {}


settings = Settings()

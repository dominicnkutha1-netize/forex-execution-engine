"""Tiny SQLite ledger: trades, closed P/L, equity snapshots.

Everything the dashboard shows comes from here, so it survives restarts
and keeps working even if MT5 history gets truncated.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket      INTEGER,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    volume      REAL NOT NULL,
    entry       REAL NOT NULL,
    sl          REAL,
    tp          REAL,
    opened_at   TEXT NOT NULL,
    closed_at   TEXT,
    exit        REAL,
    pnl         REAL,
    reason      TEXT,
    mode        TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    ts    TEXT PRIMARY KEY,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value REAL
);
"""


class Ledger:
    def __init__(self, path: str | Path = "bot.db") -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    # ------------------------------------------------------------------ kv
    def _get_kv(self, key: str) -> float | None:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else float(row["value"])

    def _set_kv(self, key: str, value: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def day_start_equity(self, equity: float) -> float:
        """First call of the day stores today's opening equity."""
        key = f"daystart:{date.today().isoformat()}"
        with self._lock:
            current = self._get_kv(key)
        if current is None:
            self._set_kv(key, equity)
            return equity
        return current

    # -------------------------------------------------------------- trades
    def open_trade(self, ticket, symbol, side, volume, entry, sl, tp, mode) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO trades(ticket, symbol, side, volume, entry, sl, tp, opened_at, mode) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (ticket, symbol, side, volume, entry, sl, tp,
                 datetime.now(timezone.utc).isoformat(), mode),
            )
            return int(cur.lastrowid)

    def close_trade(self, ticket: int, exit_price: float, pnl: float, reason: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE trades SET closed_at = ?, exit = ?, pnl = ?, reason = ? "
                "WHERE ticket = ? AND closed_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), exit_price, pnl, reason, ticket),
            )

    def pnl_since(self, since_iso: str) -> float:
        row = self._db.execute(
            "SELECT COALESCE(SUM(pnl), 0) AS total FROM trades "
            "WHERE closed_at IS NOT NULL AND closed_at >= ?",
            (since_iso,),
        ).fetchone()
        return float(row["total"])

    def open_trades(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM trades WHERE closed_at IS NULL ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_trades(self, limit: int = 50) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- equity
    def snapshot(self, equity: float) -> None:
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO equity(ts, value) VALUES(?, ?) "
                "ON CONFLICT(ts) DO UPDATE SET value = excluded.value",
                (ts, equity),
            )

    def equity_series(self, limit: int = 500) -> list[dict]:
        rows = self._db.execute(
            "SELECT ts, value FROM equity ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"time": r["ts"], "value": r["value"]} for r in reversed(rows)]

    def close(self) -> None:
        self._db.close()

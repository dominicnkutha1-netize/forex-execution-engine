"""Dashboard API.  Run with:

    python -m uvicorn api:app --port 8000

Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
from fastapi import FastAPI
from fastapi.responses import FileResponse

from config import settings
from engine import Engine
from ledger import Ledger
from mt5_gateway import MT5Gateway
from risk import RiskManager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("api")
WEB = Path(__file__).resolve().parent / "web"

gateway = MT5Gateway(
    settings.timeframe, settings.magic, settings.deviation,
    settings.allow_live, settings.mt5_path,
)
ledger = Ledger(settings.db_path)
risk = RiskManager(settings, gateway, ledger)
engine = Engine(settings, gateway, ledger, risk)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        info = gateway.connect()
        log.info("MT5 connected: %s @ %s balance=%.2f", info.login, info.server, info.balance)
    except Exception as exc:
        engine.last_error = str(exc)
        log.error("MT5 not connected: %s", exc)

    if settings.auto_trade and not engine.last_error:
        engine.start()
        log.info("auto-trade enabled")

    yield

    engine.stop()
    gateway.shutdown()


app = FastAPI(title="Forex bot", lifespan=lifespan)


def _position(p) -> dict:
    return {
        "ticket": p.ticket,
        "symbol": p.symbol,
        "side": "buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
        "volume": p.volume,
        "entry": p.price_open,
        "current": p.price_current,
        "sl": p.sl,
        "tp": p.tp,
        "profit": round(p.profit, 2),
    }


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/state")
def state() -> dict:
    payload = {
        "connected": False,
        "mode": settings.mode,
        "auto_trade": settings.auto_trade,
        "paused": engine.paused,
        "status": engine.status,
        "error": engine.last_error,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "symbols": list(settings.symbols),
        "notes": engine.notes,
        "decisions": engine.decisions,
        "strategy": {
            "timeframe": settings.timeframe,
            "session": f"{settings.session_start_utc:02d}:00-{settings.session_end_utc:02d}:00 UTC",
            "max_open": settings.max_open_trades,
            "daily_loss_pct": round(settings.max_daily_loss * 100, 2),
            "params": {s: settings.params_for(s) for s in settings.symbols},
        },
        "positions": [],
        "trades": ledger.recent_trades(25),
        "equity": ledger.equity_series(400),
        "account": None,
        "floating": 0.0,
        "day_pnl": 0.0,
    }
    try:
        acc = gateway.account()
        payload["connected"] = True
        payload["account"] = {
            "login": acc.login,
            "server": acc.server,
            "currency": acc.currency,
            "balance": round(acc.balance, 2),
            "equity": round(acc.equity, 2),
            "margin": round(acc.margin, 2),
            "free_margin": round(acc.margin_free, 2),
            "leverage": acc.leverage,
            "demo": acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_REAL,
        }
        positions = gateway.positions()
        payload["positions"] = [_position(p) for p in positions]
        payload["floating"] = round(sum(p.profit for p in positions), 2)
        payload["day_pnl"] = ledger.pnl_since(
            datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        )
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


@app.post("/api/pause")
def pause() -> dict:
    engine.pause()
    return {"ok": True, "paused": True}


@app.post("/api/resume")
def resume() -> dict:
    engine.resume()
    return {"ok": True, "paused": False}


@app.post("/api/kill")
def kill() -> dict:
    results = engine.kill()
    return {"ok": all(r.ok for r in results) if results else True, "closed": len(results)}


@app.get("/api/symbols")
def symbols() -> dict:
    return {"symbols": [gateway.symbol(s) for s in settings.symbols]}

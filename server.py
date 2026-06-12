"""
APEX Trading Engine — FastAPI Web Server (Exness MT5)
Auto-starts engine on launch. WebSocket pushes state every 3 seconds.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from engine import TradingEngine
from config import AppConfig, config as default_config

logger = logging.getLogger(__name__)

app = FastAPI(title="APEX Exness Engine", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

engine: Optional[TradingEngine] = None
_ws_clients: List[WebSocket] = []


def get_engine() -> TradingEngine:
    global engine
    if engine is None:
        engine = TradingEngine(default_config)
    return engine


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        await ws.send_text(json.dumps(get_engine().get_state()))
        while True:
            await asyncio.sleep(3)
            await ws.send_text(json.dumps(get_engine().get_state()))
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


@app.get("/api/state")
async def get_state():
    return get_engine().get_state()

@app.get("/api/signals")
async def get_signals():
    return {"signals": [s.to_dict() for s in get_engine().signals]}

@app.get("/api/positions")
async def get_positions():
    e       = get_engine()
    all_pos = e.order_manager.get_all_positions()
    return {
        "open":   [p.to_dict() for p in all_pos if p.is_open],
        "closed": [p.to_dict() for p in all_pos if not p.is_open],
    }

@app.get("/api/stats")
async def get_stats():
    return get_engine().risk_manager.get_stats().to_dict()

@app.get("/api/history")
async def get_history(limit: int = 50):
    return {"trades": get_engine().order_manager.load_trade_history(limit)}

@app.post("/api/engine/start")
async def start_engine():
    e = get_engine()
    if not e._running:
        e.start(force_scan=True)
        return {"status": "started"}
    return {"status": "already_running"}

@app.post("/api/engine/stop")
async def stop_engine():
    get_engine().stop()
    return {"status": "stopped"}

@app.post("/api/engine/scan")
async def manual_scan(background_tasks: BackgroundTasks):
    def run():
        try:
            sigs = get_engine().scan_once(force=True)
            logger.info(f"Manual scan complete: {len(sigs)} signal(s)")
        except Exception as e:
            logger.error(f"Manual scan error: {e}", exc_info=True)
    background_tasks.add_task(run)
    return {"status": "scan_started"}

@app.post("/api/engine/emergency_stop")
async def emergency_stop():
    get_engine().emergency_stop()
    return {"status": "emergency_stop_executed"}

@app.post("/api/positions/{signal_id}/close")
async def close_position(signal_id: str):
    e   = get_engine()
    pos = e.order_manager.positions.get(signal_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    price   = e.order_manager._get_price(pos.ticker) or pos.current_price
    success = e.order_manager.close_position(signal_id, price, "manual_close")
    return {"status": "closed" if success else "failed"}

@app.put("/api/config/tickers")
async def update_tickers(body: dict):
    tickers = body.get("tickers", [])
    if not isinstance(tickers, list):
        raise HTTPException(status_code=400, detail="tickers must be a list")
    default_config.data.tickers = [t.upper().strip() for t in tickers]
    return {"tickers": default_config.data.tickers}

@app.put("/api/config/risk")
async def update_risk(body: dict):
    cfg = default_config.risk
    if "max_risk_per_trade_pct" in body: cfg.fixed_risk_pct     = float(body["max_risk_per_trade_pct"])
    if "max_daily_loss_pct"     in body: cfg.max_daily_loss_pct = float(body["max_daily_loss_pct"])
    if "max_open_positions"     in body: cfg.max_open_positions  = int(body["max_open_positions"])
    return {"status": "updated"}

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent / "dashboard" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text())
    return HTMLResponse("<h1>Dashboard not found — check dashboard/index.html</h1>")

@app.on_event("startup")
async def startup_event():
    e = get_engine()
    if not e._running:
        e.start(force_scan=True)
        logger.info("Engine auto-started on server startup")

def run_server(host: str = "0.0.0.0", port: int = 8080):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info(f"Starting APEX Exness server on http://localhost:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")

if __name__ == "__main__":
    run_server()

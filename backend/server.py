import os
import json
import logging

from fastapi import FastAPI, APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse
from starlette.middleware.cors import CORSMiddleware

from database import db
from alpaca import MockAlpaca
from agent import (run_cycle, seed_demo, get_config, get_agent_state,
                   market_status, mark_positions)
from llm import chat_stream
from models import RiskConfig, now_iso, new_id

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("options_alpha")

app = FastAPI(title="Options Alpha Agent")
api = APIRouter(prefix="/api")
alpaca = MockAlpaca(db)


@app.on_event("startup")
async def startup():
    await alpaca.ensure_seed()
    try:
        await seed_demo(db, alpaca)
    except Exception as e:  # noqa
        logger.error(f"seed_demo failed: {e}")


@api.get("/")
async def root():
    return {"service": "Options Alpha Agent", "status": "online", "mode": os.environ.get("ALPACA_MODE", "mock")}


@api.get("/account")
async def account():
    await mark_positions(db, alpaca)
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    equity, bp = await alpaca.recompute_equity(open_pos)
    acc = await alpaca.get_account()
    closed = await db.positions.find({"status": "closed"}, {"_id": 0}).to_list(1000)
    wins = [c for c in closed if c["realized_pnl"] > 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0.0
    day_pnl = round(equity - acc.get("day_start_equity", equity), 2)
    day_start = acc.get("day_start_equity", equity) or equity
    open_risk = round(sum(p["max_risk"] for p in open_pos), 2)
    risk_cap = equity * 0.10
    return {
        "equity": equity, "buying_power": bp, "cash": acc["cash"],
        "initial_equity": acc["initial_equity"],
        "total_pnl": round(equity - acc["initial_equity"], 2),
        "total_pnl_pct": round((equity / acc["initial_equity"] - 1) * 100, 2),
        "day_pnl": day_pnl,
        "day_pnl_pct": round(day_pnl / day_start * 100, 2) if day_start else 0.0,
        "open_risk": open_risk,
        "open_risk_pct": round(open_risk / risk_cap * 100, 1) if risk_cap else 0.0,
        "open_positions": len(open_pos), "win_rate": win_rate,
        "total_trades": len(closed), "account_id": "PA-ALPHA-PAPER-100K",
        "mode": os.environ.get("ALPACA_MODE", "mock"),
    }


@api.get("/positions")
async def positions():
    await mark_positions(db, alpaca)
    return await db.positions.find({"status": "open"}, {"_id": 0}).sort("opened_at", -1).to_list(200)


@api.get("/trades")
async def trades():
    return await db.positions.find({"status": "closed"}, {"_id": 0}).sort("closed_at", -1).to_list(300)


@api.get("/decisions")
async def decisions(limit: int = 60):
    return await db.decisions.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)


@api.get("/pnl")
async def pnl():
    snaps = await db.pnl_snapshots.find({}, {"_id": 0}).sort("ts", 1).to_list(2000)
    return snaps


@api.get("/market")
async def market():
    m = await alpaca.get_market()
    out = []
    for s, d in m.items():
        out.append({"symbol": s, "price": d["price"],
                    "change_pct": round((d["price"] / d["day_open"] - 1) * 100, 2),
                    "iv": d["iv"], "trend": d["trend"]})
    return {"status": market_status(), "symbols": out}


@api.get("/status")
async def status():
    st = await get_agent_state(db)
    return {"agent": st, "market": market_status()}


@api.get("/config")
async def config():
    return await get_config(db)


@api.put("/config")
async def update_config(payload: dict = Body(...)):
    cur = await get_config(db)
    cur.update({k: v for k, v in payload.items() if k in RiskConfig.model_fields and k != "id"})
    clean = RiskConfig(**cur).model_dump()
    await db.config.update_one({"id": "risk_config"}, {"$set": clean}, upsert=True)
    return clean


@api.post("/agent/run-cycle")
async def agent_run_cycle(payload: dict = Body(default={})):
    force = bool(payload.get("force", False))
    result = await run_cycle(db, alpaca, force=force)
    return result


@api.post("/agent/pause")
async def agent_pause(payload: dict = Body(default={})):
    paused = bool(payload.get("paused", True))
    await db.agent_state.update_one({"id": "agent_state"}, {"$set": {"paused": paused}}, upsert=True)
    return {"paused": paused}


@api.post("/positions/{position_id}/close")
async def close_position(position_id: str):
    await mark_positions(db, alpaca)
    p = await db.positions.find_one({"id": position_id, "status": "open"}, {"_id": 0})
    if not p:
        raise HTTPException(status_code=404, detail="position not found or already closed")
    await alpaca.apply_equity_delta(p["unrealized_pnl"])
    await db.positions.update_one({"id": position_id}, {"$set": {
        "status": "closed", "exit_reason": "manual", "realized_pnl": p["unrealized_pnl"],
        "closed_at": now_iso()}})
    await db.decisions.insert_one({
        "id": new_id(), "cycle_id": "manual", "underlying": p["underlying"],
        "strategy": p["strategy"], "outcome": "approved", "gate_passed": True,
        "gate_checks": [], "reason": f"MANUAL CLOSE {p['strategy']} realized ${p['unrealized_pnl']:+,.0f}",
        "position_id": position_id, "created_at": now_iso()})
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    await alpaca.recompute_equity(open_pos)
    return {"closed": True, "realized_pnl": p["unrealized_pnl"]}


@api.post("/chat")
async def chat(payload: dict = Body(...)):
    question = payload.get("message", "")
    session_id = payload.get("session_id", "operator")
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(50)
    acc = await alpaca.get_account()
    recent = await db.decisions.find({}, {"_id": 0}).sort("created_at", -1).to_list(8)
    context = {
        "account": {"equity": acc["equity"], "buying_power": acc["buying_power"], "cash": acc["cash"]},
        "open_positions": [{"underlying": p["underlying"], "strategy": p["strategy"],
                            "legs": [f"{l['side']} {l['strike']}{l['option_type'][0].upper()}" for l in p["legs"]],
                            "credit": p["credit"], "contracts": p["contracts"],
                            "max_risk": p["max_risk"], "unrealized_pnl": p["unrealized_pnl"],
                            "dte": p["dte"]} for p in open_pos],
        "recent_decisions": [{"underlying": d["underlying"], "outcome": d["outcome"],
                              "reason": d["reason"]} for d in recent],
    }

    async def gen():
        try:
            async for chunk in chat_stream(question, context, session_id):
                yield chunk
        except Exception as e:  # noqa
            yield f"\n[agent error: {e}]"

    return StreamingResponse(gen(), media_type="text/plain",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


app.include_router(api)
app.add_middleware(
    CORSMiddleware, allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown():
    pass

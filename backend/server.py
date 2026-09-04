import os
import sys
import json
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import traceback

from fastapi import FastAPI, APIRouter, Body, HTTPException, Header, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from database import db
from alpaca import make_alpaca, UNIVERSE
from agent import (run_cycle, seed_demo, get_config, get_agent_state,
                   market_status, mark_positions, close_position as close_spread)
from engines import build_spread, risk_gate
from llm import chat_stream, get_verdict
from models import RiskConfig, Position, Decision, now_iso, new_id

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("options_alpha")

app = FastAPI(title="Options Alpha Agent")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error("Unhandled error processing %s %s: %s\n%s", request.method, request.url.path, exc, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc), "path": request.url.path}
    )
api = APIRouter(prefix="/api")
alpaca = make_alpaca(db)
CYCLE_SECONDS = int(os.environ.get("AGENT_CYCLE_SECONDS", "900"))
SERVERLESS = bool(os.environ.get("VERCEL"))
TICK_MAX_CANDIDATES = int(os.environ.get("TICK_MAX_CANDIDATES", "3"))
FRONTEND_BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "build")


async def autonomous_loop():
    """In-process scheduler: one agent cycle every CYCLE_SECONDS while the market is open."""
    while True:
        await asyncio.sleep(CYCLE_SECONDS)
        try:
            st = await get_agent_state(db)
            if st.get("paused") or not st.get("autonomous", True):
                continue
            if await alpaca.market_open():
                res = await run_cycle(db, alpaca)
                logger.info(f"[auto] cycle {res.get('cycle_id')} {res.get('status')} decisions={len(res.get('decisions', []))}")
        except Exception as e:  # noqa
            logger.error(f"[auto] cycle failed: {e}")


@app.on_event("startup")
async def startup():
    try:
        await alpaca.ensure_seed()
    except Exception as e:
        logger.error(f"ensure_seed failed on startup: {e}")
    if alpaca.mode == "mock":
        try:
            await seed_demo(db, alpaca)
        except Exception as e:  # noqa
            logger.error(f"seed_demo failed: {e}")
    if not SERVERLESS:
        asyncio.create_task(autonomous_loop())


@api.get("/")
@api.get("/health")
async def root():
    return {"service": "Options Alpha Agent", "status": "online", "mode": alpaca.mode}


@api.get("/debug")
async def debug_endpoint():
    return {
        "status": "ok",
        "service": "Options Alpha Agent",
        "mode": alpaca.mode,
        "python": sys.version,
        "cwd": os.getcwd(),
        "files_root": os.listdir(".") if os.path.exists(".") else [],
        "sys_path": sys.path,
        "env_has_alpaca_key": bool(os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")),
        "env_has_alpaca_secret": bool(os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET") or os.environ.get("APCA_API_SECRET_KEY")),
        "env_has_featherless_key": bool(os.environ.get("FEATHERLESS_API_KEY")),
        "env_keys": [k for k in os.environ.keys() if "KEY" not in k and "SECRET" not in k],
    }


@api.get("/mcp/status")
async def mcp_status():
    acc = await alpaca.get_account() or {}
    return {
        "status": "online",
        "protocol": "Model Context Protocol (MCP)",
        "servers": {
            "alpaca": {
                "name": "Official Alpaca MCP Server",
                "package": "alpaca-mcp-server v2.3.1",
                "mode": "paper",
                "account_id": acc.get("account_number", "PA39X74UN8VF"),
                "status": "active"
            },
            "petra_alpha": {
                "name": "Petra Options Alpha MCP Server",
                "status": "active",
                "tools": [
                    "get_alpaca_account",
                    "get_market_universe",
                    "get_options_chain",
                    "get_open_positions",
                    "evaluate_risk_gate",
                    "trigger_agent_cycle",
                    "reconcile_positions"
                ]
            }
        },
        "config": ".mcp.json"
    }


@api.get("/models")
async def get_models():
    models_file = Path(__file__).parent / "featherless_models.json"
    categories = {}
    if models_file.exists():
        try:
            with open(models_file, "r", encoding="utf-8") as f:
                categories = json.load(f)
        except Exception:
            pass
    active_model = os.environ.get("FEATHERLESS_MODEL", "Qwen/Qwen3.6-35B-A3B")
    has_featherless = bool(os.environ.get("FEATHERLESS_API_KEY"))
    return {
        "provider": "Featherless AI" if has_featherless else "Emergent / Fallback",
        "active_model": active_model,
        "endpoint": os.environ.get("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1"),
        "curated_categories": categories
    }


@api.get("/account")
async def account():
    await mark_positions(db, alpaca)
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    equity, bp = await alpaca.recompute_equity(open_pos)
    acc = (await alpaca.get_account()) or {}
    closed = await db.positions.find({"status": "closed"}, {"_id": 0}).to_list(1000)
    wins = [c for c in closed if c["realized_pnl"] > 0]
    win_rate = round(len(wins) / len(closed) * 100, 1) if closed else 0.0
    day_start = acc.get("day_start_equity", equity) or equity
    day_pnl = round(equity - day_start, 2)
    open_risk = round(sum(p["max_risk"] for p in open_pos), 2)
    risk_cap = equity * 0.10
    init_eq = acc.get("initial_equity", equity) or equity
    return {
        "equity": equity, "buying_power": bp, "cash": acc.get("cash", equity),
        "initial_equity": init_eq,
        "total_pnl": round(equity - init_eq, 2),
        "total_pnl_pct": round((equity / init_eq - 1) * 100, 2) if init_eq else 0.0,
        "day_pnl": day_pnl,
        "day_pnl_pct": round(day_pnl / day_start * 100, 2) if day_start else 0.0,
        "open_risk": open_risk,
        "open_risk_pct": round(open_risk / risk_cap * 100, 1) if risk_cap else 0.0,
        "open_positions": len(open_pos), "win_rate": win_rate,
        "total_trades": len(closed), "account_id": acc.get("account_number", "—"),
        "mode": alpaca.mode,
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
    acc = (await alpaca.get_account()) or {}
    init_eq = acc.get("initial_equity", 100000.0)
    base = next((s["spy"] for s in snaps if s.get("spy")), None)
    for s in snaps:
        s["benchmark"] = round(init_eq * s["spy"] / base, 2) if base and s.get("spy") else None
    return snaps


@api.get("/orders")
async def orders(limit: int = 100):
    return await db.orders.find({}, {"_id": 0}).sort("ts", -1).to_list(limit)


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
    return {"agent": st, "market": market_status(), "mode": alpaca.mode, "cycle_seconds": CYCLE_SECONDS,
            "last_reconcile": getattr(alpaca, "_last_reconcile", None)}


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


@api.api_route("/agent/tick", methods=["GET", "POST"])
async def agent_tick(authorization: str = Header(default="")):
    """External scheduler hook (Vercel Cron / GitHub Actions). Runs one cycle if the market is open."""
    secret = os.environ.get("CRON_SECRET")
    if secret and authorization != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="bad cron secret")
    st = await get_agent_state(db)
    if st.get("paused") or not st.get("autonomous", True):
        return {"status": "paused"}
    if not await alpaca.market_open():
        return {"status": "market_closed"}
    res = await run_cycle(db, alpaca, max_candidates=TICK_MAX_CANDIDATES)
    return {"status": res["status"], "cycle_id": res["cycle_id"],
            "decisions": len(res.get("decisions", [])), "exits": len(res.get("exits", []))}


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
    res = await close_spread(db, alpaca, p, "manual", "manual")
    if not res["closed"]:
        raise HTTPException(status_code=502, detail=f"close order {res['status']} — position still open")
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    await alpaca.recompute_equity(open_pos)
    return res


@api.post("/opportunities/evaluate")
async def evaluate_opportunity(payload: dict = Body(...)):
    """Evaluate any underlying symbol on-demand: fetches live chain, runs LLM verdict, strike engine, and 7 risk gates."""
    symbol = payload.get("symbol", "SPY").upper()
    if symbol not in UNIVERSE:
        raise HTTPException(status_code=400, detail=f"Symbol {symbol} not in universe {list(UNIVERSE.keys())}")
    cfg = await get_config(db)
    acc = await alpaca.get_account()
    market = await alpaca.get_market()
    m = market.get(symbol) or {"price": 500.0, "day_open": 500.0, "iv": 0.2, "trend": 0.0, "spacing": 1.0}
    try:
        chain = await alpaca.load_chain(symbol, m["price"], cfg)
    except Exception:
        chain = None
    if chain:
        m.update({k: chain[k] for k in ("iv", "spacing") if chain.get(k)})

    live = alpaca.mode == "live"
    snap = {
        "symbol": symbol,
        "price": m["price"],
        "prev_price": m.get("prev_price", m["price"]),
        "change_pct": round((m["price"] / m["day_open"] - 1) * 100, 2) if m.get("day_open") else 0.0,
        "iv": m["iv"],
        "trend": m["trend"],
        "trend_label": "Change vs prior close" if live else "5-step trend bias",
        "sentiment": "Live market scan" if live else "Operator request"
    }
    if chain:
        snap["expiry"] = chain["expiry"]

    verdict = await get_verdict(snap, f"eval-{new_id()[:6]}")
    proposal = build_spread(alpaca, symbol, m["price"], m["iv"], m["spacing"], verdict, cfg, acc["equity"])
    if not proposal:
        return {
            "symbol": symbol, "market": snap, "verdict": verdict,
            "proposal": None, "gate_checks": [], "gate_passed": False, "gate_score": 0,
            "error": "Strike engine could not construct valid strikes for this underlying/regime."
        }

    cur_open = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    checks, passed, score = risk_gate(proposal, cfg, acc["equity"], cur_open)

    return {
        "symbol": symbol,
        "market": snap,
        "verdict": verdict,
        "proposal": proposal,
        "gate_checks": checks,
        "gate_passed": passed,
        "gate_score": score
    }


@api.post("/positions/open")
async def manual_open_position(payload: dict = Body(...)):
    """Open a position manually after reviewing agent feedback and risk."""
    proposal = payload.get("proposal")
    decision_id = payload.get("decision_id")
    override_contracts = payload.get("contracts")
    paper_sim = bool(payload.get("paper_sim", True))

    if not proposal and decision_id:
        dec = await db.decisions.find_one({"id": decision_id}, {"_id": 0})
        if dec and dec.get("proposal"):
            proposal = dec["proposal"]

    if not proposal:
        raise HTTPException(status_code=400, detail="Missing trade proposal data")

    cfg = await get_config(db)

    if override_contracts and int(override_contracts) > 0:
        proposal["contracts"] = min(int(override_contracts), 10)
        risk_per = (proposal["width"] - proposal["credit"]) * 100
        proposal["max_risk"] = round(risk_per * proposal["contracts"], 2)

    cycle_id = f"man-{new_id()[:6]}"
    try:
        order = await alpaca.place_mleg(proposal, cycle_id=cycle_id)
    except Exception as e:
        order = {"order_id": "", "status": "error", "alpaca_status": str(e)[:200], "filled_credit": 0}

    if order.get("status") == "filled":
        credit = round(order.get("filled_credit") or proposal["credit"], 2)
        order_id = order.get("order_id", f"mleg-{new_id()[:8]}")
        fill_status = "filled"
    elif paper_sim:
        credit = proposal["credit"]
        order_id = order.get("order_id") or f"paper-sim-{new_id()[:8]}"
        fill_status = "simulated_paper_fill"
    else:
        raise HTTPException(status_code=400, detail=f"Order not filled: {order.get('alpaca_status', order.get('status'))}")

    credit_cash = credit * 100 * proposal["contracts"]
    pos = Position(
        underlying=proposal["underlying"],
        strategy=proposal["strategy"],
        legs=proposal["legs"],
        contracts=proposal["contracts"],
        width=proposal["width"],
        credit=credit,
        max_risk=proposal["max_risk"],
        entry_underlying=proposal.get("entry_underlying", 0.0),
        entry_iv=proposal.get("entry_iv", 0.2),
        dte=proposal["dte"],
        expiry_ts=proposal["expiry_ts"],
        tp_target=round(credit * (1 - cfg["tp_pct"]), 2),
        stop_target=round(credit * cfg["stop_mult"], 2),
        current_value=credit,
        risk_gate_score=100,
        alpaca_order_id=order_id,
        paper_sim=fill_status == "simulated_paper_fill"
    )
    await db.positions.insert_one(pos.model_dump())

    dec = Decision(
        cycle_id=cycle_id,
        underlying=proposal["underlying"],
        strategy=proposal["strategy"],
        outcome="approved",
        gate_passed=True,
        reason=f"MANUAL EXECUTION: {proposal['strategy']} x{proposal['contracts']} — credit ${credit_cash:,.0f} (order {order_id})",
        position_id=pos.id,
        proposed=proposal,
        proposal=proposal
    )
    await db.decisions.insert_one(dec.model_dump())

    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    await alpaca.recompute_equity(open_pos)

    return {
        "success": True,
        "position": pos.model_dump(),
        "order": order,
        "fill_status": fill_status,
        "message": f"Opened {proposal['strategy']} x{proposal['contracts']} on {proposal['underlying']}"
    }


@api.post("/chat")
async def chat(payload: dict = Body(...)):
    question = payload.get("message", "")
    session_id = payload.get("session_id", "operator")
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(50)
    acc = (await alpaca.get_account()) or {}
    recent = await db.decisions.find({}, {"_id": 0}).sort("created_at", -1).to_list(8)
    context = {
        "account": {"equity": acc.get("equity", 100000.0), "buying_power": acc.get("buying_power", 100000.0), "cash": acc.get("cash", 100000.0)},
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
if not SERVERLESS and os.path.isdir(FRONTEND_BUILD):
    app.mount("/", StaticFiles(directory=FRONTEND_BUILD, html=True), name="frontend")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[o.strip() for o in os.environ.get('CORS_ORIGINS', '*').split(',') if o.strip()],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown():
    pass

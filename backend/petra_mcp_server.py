"""Petra Options Alpha Agent - Model Context Protocol (MCP) Server.

Exposes Petra's deterministic strike engine, risk gates, position management,
and Alpaca live paper market/account data as standard MCP tools.
Compatible with Claude Desktop, Cursor, and any MCP-compliant client.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure backend root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from fastmcp import FastMCP
from database import db
from alpaca import make_alpaca
from agent import (run_cycle, get_agent_state, get_config,
                   mark_positions, market_status)
from engines import risk_gate, build_spread

alpaca = make_alpaca(db)
mcp = FastMCP("petra-options-alpha-agent")


@mcp.tool()
async def get_alpaca_account() -> dict:
    """Retrieve live Alpaca paper trading account status, equity, buying power, and cash."""
    await alpaca.ensure_seed()
    acc = await alpaca.get_account()
    if not acc:
        return {"error": "Account not initialized"}
    return {
        "account_id": acc.get("account_number"),
        "mode": acc.get("mode", "live"),
        "equity": acc.get("equity"),
        "buying_power": acc.get("buying_power"),
        "cash": acc.get("cash"),
        "day_start_equity": acc.get("day_start_equity"),
        "updated_at": acc.get("updated_at")
    }


@mcp.tool()
async def get_market_universe() -> dict:
    """Get current market data snapshot for Petra's trading universe (SPY, QQQ, IWM, AAPL, MSFT, NVDA, TSLA, META)."""
    symbols = await alpaca.get_market()
    return {"symbols": symbols, "market_open": await alpaca.market_open()}


@mcp.tool()
async def get_options_chain(underlying: str) -> dict:
    """Load active options chain for an underlying, including nearest expiration, strike spacing, and IV."""
    u = underlying.upper()
    m = await alpaca.get_market()
    if u not in m:
        return {"error": f"Underlying {u} not in universe"}
    cfg = await get_config(db)
    res = await alpaca.load_chain(u, m[u]["price"], cfg)
    if not res:
        return {"error": f"Could not load chain for {u}"}
    return {"underlying": u, "chain_info": res}


@mcp.tool()
async def get_open_positions() -> list:
    """List all currently active credit spread positions with real-time marks, TP/SL targets, and unrealized P&L."""
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    await mark_positions(db, alpaca, open_pos)
    return await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)


@mcp.tool()
async def evaluate_risk_gate(
    underlying: str,
    strategy: str,
    contracts: int,
    credit: float,
    short_strike: float,
    long_strike: float
) -> dict:
    """Evaluate Petra's 7 deterministic hard risk checks on a proposed options spread without placing an order."""
    acc = await alpaca.get_account()
    cfg = await get_config(db)
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    width = abs(short_strike - long_strike)
    max_loss = round((width - credit) * 100 * contracts, 2)
    proposal = {
        "underlying": underlying.upper(),
        "strategy": strategy,
        "contracts": contracts,
        "credit": credit,
        "width": width,
        "max_loss": max_loss,
        "legs": [
            {"symbol": f"{underlying}_SHORT", "side": "sell", "strike": short_strike, "mid": credit, "bid_ask_pct": 0.05, "open_interest": 1000},
            {"symbol": f"{underlying}_LONG", "side": "buy", "strike": long_strike, "mid": 0.05, "bid_ask_pct": 0.05, "open_interest": 1000}
        ]
    }
    decision = risk_gate(proposal, acc["equity"], open_pos, cfg)
    return {
        "passed": decision.gate_passed,
        "outcome": decision.outcome,
        "reason": decision.reason,
        "gate_checks": decision.gate_checks
    }


@mcp.tool()
async def trigger_agent_cycle(force: bool = False) -> dict:
    """Trigger an autonomous agent cycle immediately. If market is closed, set force=True to evaluate candidates."""
    res = await run_cycle(db, alpaca, force=force)
    return {
        "cycle_id": res.get("cycle_id"),
        "status": res.get("status"),
        "decisions_count": len(res.get("decisions", [])),
        "decisions": res.get("decisions", [])
    }


@mcp.tool()
async def reconcile_positions() -> dict:
    """Reconcile internal database positions against live Alpaca /v2/positions to detect assignments, expirations, and orphans."""
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    events = await alpaca.reconcile(open_pos, force=True)
    return {"reconciled_events": events, "active_positions_count": len(open_pos)}


if __name__ == "__main__":
    mcp.run()

"""Agent orchestration: position management + trade cycle + demo seeding."""
import random
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from alpaca import MockAlpaca, UNIVERSE
from pricing import bs_price, bs_delta
from engines import build_spread, risk_gate
from llm import get_verdict
from models import Position, Decision, RiskConfig, AgentState, now_iso, new_id

ET = ZoneInfo("America/New_York")
SENTIMENTS = ["neutral wire flow", "mild bullish tape", "cautious / mixed headlines",
              "risk-off chatter", "earnings drift", "macro data pending", "steady bid"]


def market_status():
    now = datetime.now(ET)
    is_weekday = now.weekday() < 5
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    is_open = is_weekday and open_t <= now <= close_t
    if is_open:
        to_close = close_t - now
        return {"open": True, "session": "REGULAR", "message": "NYSE / NASDAQ: OPEN",
                "countdown": str(to_close).split(".")[0], "et_time": now.strftime("%H:%M:%S ET")}
    nxt = open_t if now < open_t and is_weekday else open_t + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return {"open": False, "session": "CLOSED", "message": "NYSE / NASDAQ: CLOSED",
            "countdown": str(nxt - now).split(".")[0], "et_time": now.strftime("%H:%M:%S ET")}


async def get_config(db):
    doc = await db.config.find_one({"id": "risk_config"}, {"_id": 0})
    if not doc:
        doc = RiskConfig().model_dump()
        await db.config.insert_one(doc)
    return doc


async def get_agent_state(db):
    doc = await db.agent_state.find_one({"id": "agent_state"}, {"_id": 0})
    if not doc:
        doc = AgentState().model_dump()
        await db.agent_state.insert_one(doc)
    return doc


def _spread_close_value(p, market):
    """Current net debit (per share) to buy the spread back, marked to model."""
    S = market[p["underlying"]]["price"]
    iv = market[p["underlying"]]["iv"]
    remain = max(0.0, (datetime.fromisoformat(p["expiry_ts"]) - datetime.now(timezone.utc)).total_seconds() / 86400.0)
    T = max(remain, 0.02) / 365.0
    val = 0.0
    for leg in p["legs"]:
        is_call = leg["option_type"] == "call"
        price = bs_price(S, leg["strike"], T, iv, is_call)
        val += price if leg["side"] == "sell" else -price
    return round(max(0.0, val), 2), remain


async def mark_positions(db, alpaca):
    market = await alpaca.get_market()
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    for p in open_pos:
        close_val, remain = _spread_close_value(p, market)
        pnl = round((p["credit"] - close_val) * 100 * p["contracts"], 2)
        max_p = p["credit"] * 100 * p["contracts"]
        pct = round((pnl / max_p) * 100, 1) if max_p else 0.0
        await db.positions.update_one({"id": p["id"]}, {"$set": {
            "current_value": close_val, "unrealized_pnl": pnl,
            "unrealized_pct": pct, "dte": round(remain, 2)}})
        p.update({"current_value": close_val, "unrealized_pnl": pnl, "unrealized_pct": pct, "dte": remain})
    return open_pos, market


async def manage_positions(db, alpaca, cfg, cycle_id):
    """Mark, then apply TP / stop / time-based exits."""
    open_pos, market = await mark_positions(db, alpaca)
    events = []
    for p in open_pos:
        reason = None
        if p["current_value"] <= p["credit"] * (1 - cfg["tp_pct"]):
            reason = "take_profit"
        elif p["current_value"] >= p["credit"] * cfg["stop_mult"]:
            reason = "stop_loss"
        elif p["dte"] <= 0.75:
            reason = "time_exit"
        if reason:
            await _close_position(db, alpaca, p, reason, cycle_id)
            events.append({"underlying": p["underlying"], "reason": reason, "pnl": p["unrealized_pnl"]})
    return events


async def _close_position(db, alpaca, p, reason, cycle_id):
    realized = p["unrealized_pnl"]
    await alpaca.apply_equity_delta(realized)  # realize into cash
    await db.positions.update_one({"id": p["id"]}, {"$set": {
        "status": "closed", "exit_reason": reason, "realized_pnl": realized,
        "closed_at": now_iso()}})
    await db.decisions.insert_one(Decision(
        cycle_id=cycle_id, underlying=p["underlying"], strategy=p["strategy"],
        outcome="approved", gate_passed=True,
        reason=f"EXIT [{reason}] {p['strategy']} realized ${realized:+,.0f}",
        position_id=p["id"]).model_dump())


async def open_slots(db, cfg):
    n = await db.positions.count_documents({"status": "open"})
    return max(0, cfg["max_concurrent"] - n)


async def run_cycle(db, alpaca, force=False):
    cfg = await get_config(db)
    state = await get_agent_state(db)
    cycle_id = new_id()[:8]
    mkt = market_status()

    if state.get("paused"):
        return {"cycle_id": cycle_id, "status": "paused", "message": "Agent is paused.", "decisions": []}
    if not mkt["open"] and not force:
        await db.decisions.insert_one(Decision(
            cycle_id=cycle_id, underlying="—", outcome="skipped",
            reason="Market closed — options trade in regular hours only. Agent no-op.").model_dump())
        await _bump_cycle(db, state)
        return {"cycle_id": cycle_id, "status": "market_closed", "message": mkt["message"], "decisions": []}

    # 1. advance the simulated tape + manage existing positions
    await alpaca.advance_market()
    exits = await manage_positions(db, alpaca, cfg, cycle_id)

    # 2. evaluate new candidates (LLM-gated), up to open slots, cap LLM calls at 3
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    held = {p["underlying"] for p in open_pos}
    acc = await alpaca.get_account()
    market = await alpaca.get_market()
    candidates = [s for s in UNIVERSE if s not in held]
    random.shuffle(candidates)
    slots = max(0, cfg["max_concurrent"] - len(open_pos))
    decisions_out = []

    for sym in candidates[: min(3, max(1, slots) if slots else 1)]:
        if len([d for d in decisions_out if d["outcome"] == "approved"]) >= slots:
            break
        m = market[sym]
        snap = {"symbol": sym, "price": m["price"], "prev_price": m["prev_price"],
                "change_pct": round((m["price"] / m["day_open"] - 1) * 100, 2),
                "iv": m["iv"], "trend": m["trend"], "sentiment": random.choice(SENTIMENTS)}
        verdict = await get_verdict(snap, cycle_id)

        dec = Decision(cycle_id=cycle_id, underlying=sym, verdict=verdict,
                       strategy=verdict["chosen_strategy"], market_snapshot=snap)

        if verdict["confidence"] < 0.5:
            dec.outcome = "rejected"
            dec.reason = f"Low LLM confidence {verdict['confidence']:.0%} < 50% floor. Skip trade."
            await db.decisions.insert_one(dec.model_dump())
            decisions_out.append(dec.model_dump())
            continue

        proposal = build_spread(alpaca, sym, m["price"], m["iv"], m["spacing"], verdict, cfg, acc["equity"])
        if not proposal:
            dec.outcome = "error"
            dec.reason = "Strike engine could not build a valid spread."
            await db.decisions.insert_one(dec.model_dump())
            decisions_out.append(dec.model_dump())
            continue

        cur_open = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
        checks, passed, score = risk_gate(proposal, cfg, acc["equity"], cur_open)
        dec.gate_checks = checks
        dec.gate_passed = passed
        dec.proposed = {k: proposal[k] for k in ("strategy", "contracts", "width", "credit",
                        "max_risk", "credit_width_ratio", "short_delta", "dte")}

        if not passed:
            fails = [c["label"] for c in checks if not c["passed"]]
            dec.outcome = "rejected"
            dec.reason = "Risk gate REJECTED: " + "; ".join(fails)
            await db.decisions.insert_one(dec.model_dump())
            decisions_out.append(dec.model_dump())
            continue

        # 3. execute via Alpaca (mock) mleg order
        order = await alpaca.place_mleg(proposal, dry_run=False)
        credit_cash = proposal["credit"] * 100 * proposal["contracts"]
        await alpaca.apply_equity_delta(credit_cash)  # collect credit into cash

        pos = Position(
            underlying=sym, strategy=proposal["strategy"], legs=proposal["legs"],
            contracts=proposal["contracts"], width=proposal["width"], credit=proposal["credit"],
            max_risk=proposal["max_risk"], entry_underlying=m["price"], entry_iv=m["iv"],
            dte=proposal["dte"], expiry_ts=proposal["expiry_ts"],
            tp_target=round(proposal["credit"] * (1 - cfg["tp_pct"]), 2),
            stop_target=round(proposal["credit"] * cfg["stop_mult"], 2),
            current_value=proposal["credit"], risk_gate_score=score,
            alpaca_order_id=order["order_id"])
        await db.positions.insert_one(pos.model_dump())

        dec.outcome = "approved"
        dec.position_id = pos.id
        dec.reason = (f"APPROVED {proposal['strategy']} x{proposal['contracts']} — credit "
                      f"${credit_cash:,.0f}, max risk ${proposal['max_risk']:,.0f}, order {order['order_id']}")
        await db.decisions.insert_one(dec.model_dump())
        decisions_out.append(dec.model_dump())

    # 4. recompute equity + snapshot P&L
    open_pos = await db.positions.find({"status": "open"}, {"_id": 0}).to_list(200)
    equity, bp = await alpaca.recompute_equity(open_pos)
    await db.pnl_snapshots.insert_one({
        "id": new_id(), "ts": now_iso(), "equity": equity,
        "open_risk": round(sum(p["max_risk"] for p in open_pos), 2),
        "open_positions": len(open_pos)})
    await _bump_cycle(db, state)

    return {"cycle_id": cycle_id, "status": "ran", "market": mkt,
            "exits": exits, "decisions": decisions_out,
            "equity": equity, "open_positions": len(open_pos)}


async def _bump_cycle(db, state):
    await db.agent_state.update_one({"id": "agent_state"}, {"$set": {
        "last_cycle_at": now_iso(), "total_cycles": state.get("total_cycles", 0) + 1}}, upsert=True)


# ---------------- demo seeding ----------------
async def seed_demo(db, alpaca):
    """Populate a realistic history so the terminal is alive on first load."""
    if await db.pnl_snapshots.count_documents({}) > 0:
        return
    await alpaca.ensure_seed()
    market = await alpaca.get_market()
    base = datetime.now(timezone.utc) - timedelta(hours=40)
    equity = 100000.0

    # historical equity curve (upward-biased, controlled)
    for i in range(40):
        equity += random.gauss(180, 260)
        ts = (base + timedelta(hours=i)).isoformat()
        await db.pnl_snapshots.insert_one({"id": new_id(), "ts": ts, "equity": round(equity, 2),
                                           "open_risk": round(random.uniform(2500, 6500), 2),
                                           "open_positions": random.randint(2, 5)})

    # closed trade history
    strategies = ["put_credit_spread", "call_credit_spread", "iron_condor"]
    for i in range(14):
        sym = random.choice(list(UNIVERSE))
        strat = random.choice(strategies)
        win = random.random() < 0.72
        credit = round(random.uniform(0.6, 1.8), 2)
        contracts = random.randint(1, 6)
        realized = round((credit * 0.5 if win else -credit * 1.4) * 100 * contracts, 2)
        ts = (base + timedelta(hours=random.randint(0, 38))).isoformat()
        p = Position(underlying=sym, strategy=strat, legs=[], contracts=contracts,
                     width=2.0, credit=credit, max_risk=round((2 - credit) * 100 * contracts, 2),
                     entry_underlying=market[sym]["price"], entry_iv=market[sym]["iv"], dte=3,
                     expiry_ts=ts, tp_target=credit * 0.5, stop_target=credit * 2,
                     current_value=0, status="closed",
                     exit_reason="take_profit" if win else "stop_loss",
                     realized_pnl=realized, alpaca_order_id=f"mock-{new_id()[:8]}",
                     opened_at=ts, closed_at=ts)
        await db.positions.insert_one(p.model_dump())

    await db.account.update_one({"id": "account"}, {"$set": {
        "equity": round(equity, 2), "cash": round(equity, 2),
        "buying_power": round(equity, 2), "day_start_equity": round(equity - 900, 2)}})
    # run a few live cycles so we have open positions + fresh decisions
    for _ in range(5):
        await run_cycle(db, alpaca, force=True)

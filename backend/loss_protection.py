"""Persistent entry brakes shared by autonomous and manual spread entries.

Reservations never expire: uncertain submissions require explicit reconciliation.
Exits do not acquire an entry reservation and remain available during lockouts.
"""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import wraps
from pymongo.errors import DuplicateKeyError
from models import RiskConfig, new_id, now_iso


class EntryBlocked(RuntimeError):
    pass


def positive(value):
    if isinstance(value, bool):
        raise EntryBlocked("Invalid risk data")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise EntryBlocked("Missing risk data") from None
    if not math.isfinite(number) or number <= 0:
        raise EntryBlocked("Non-positive/non-finite risk data")
    return number


async def check_losses(db, account, cfg, now=None):
    """Daily latch resets only on a later New York date; drawdown never auto-resets."""
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    equity = positive(account.get("equity"))
    baseline = positive(account.get("day_start_equity"))
    initial = positive(account.get("initial_equity"))
    daily_limit = positive(cfg.get("max_daily_loss_pct", 2.0))
    drawdown_limit = positive(cfg.get("max_drawdown_pct", 5.0))
    if daily_limit > 100 or drawdown_limit > 100:
        raise EntryBlocked("Invalid risk limits")
    # Broker-backed observations must be fresh, never a cached fallback.
    if account.get("mode") == "live":
        try:
            age = (now - datetime.fromisoformat(account["updated_at"])).total_seconds()
        except (KeyError, TypeError, ValueError):
            raise EntryBlocked("Missing account timestamp") from None
        if not 0 <= age <= 60:
            raise EntryBlocked("Stale account data")
    state = await db.risk_state.find_one({"_id": "equity"})
    if state is None:
        binding = await db.account.find_one({"id": "account"}) or {}
        if binding.get("loss_protection_initialized"):
            raise EntryBlocked("Persistent loss protection state missing; review required")
        # Recover the observed peak from persisted history on first migration.
        history = await db.pnl_snapshots.find({}, {"equity": 1}).to_list(None)
        peak = max([initial, baseline, equity] + [positive(p.get("equity")) for p in history])
        await db.risk_state.update_one({"_id": "equity"}, {"$setOnInsert": {
            "peak": peak, "drawdown_locked": False}}, upsert=True)
    else:
        positive(state.get("peak"))
        if not isinstance(state.get("drawdown_locked"), bool):
            raise EntryBlocked("Corrupt drawdown latch")
    await db.account.update_one({"id": "account"}, {"$set": {
        "loss_protection_initialized": True}}, upsert=True)
    await db.risk_state.update_one({"_id": "equity"}, {"$max": {"peak": equity}})
    state = await db.risk_state.find_one({"_id": "equity"})
    peak = positive(state.get("peak"))
    drawdown = (peak - equity) / peak * 100
    if drawdown >= drawdown_limit:
        await db.risk_state.update_one({"_id": "equity"}, {"$set": {
            "drawdown_locked": True, "breached_at": now.isoformat()}})
        state["drawdown_locked"] = True
    daily = await db.risk_state.find_one({"_id": "day:" + day})
    if daily is None and day in state.get("observed_days", []):
        raise EntryBlocked("Persistent daily loss state missing; review required")
    await db.risk_state.update_one({"_id": "day:" + day}, {"$setOnInsert": {
        "baseline": baseline, "locked": False}}, upsert=True)
    await db.risk_state.update_one({"_id": "equity"}, {"$addToSet": {"observed_days": day}})
    daily = await db.risk_state.find_one({"_id": "day:" + day})
    if not isinstance(daily.get("locked"), bool):
        raise EntryBlocked("Corrupt daily latch")
    loss = (positive(daily.get("baseline")) - equity) / positive(daily.get("baseline")) * 100
    if loss >= daily_limit:
        await db.risk_state.update_one({"_id": "day:" + day}, {"$set": {
            "locked": True, "breached_at": now.isoformat()}})
        daily["locked"] = True
    if state.get("drawdown_locked"):
        raise EntryBlocked("Maximum equity drawdown lockout; review required")
    if daily.get("locked"):
        raise EntryBlocked("Daily loss cutoff; no new risk until next trading date")


def protected_entry(method):
    """Reserve before checking risk or submitting; covers every place_mleg caller."""
    @wraps(method)
    async def guarded(self, proposal, cycle_id=""):
        db = self.db
        owner = new_id()
        old = await db.entry_reservations.find_one({"_id": "single_position"})
        if old and old.get("order_id"):
            closed = await db.positions.find_one({"alpaca_order_id": old["order_id"], "status": "closed"})
            if closed:
                await db.entry_reservations.delete_one({"_id": "single_position", "owner": old["owner"]})
        try:
            await db.entry_reservations.insert_one({"_id": "single_position", "owner": owner,
                                                    "created_at": now_iso()})
        except DuplicateKeyError:
            raise EntryBlocked("Entry pending/open/uncertain; reconciliation required") from None
        submitted = False
        try:
            if await db.positions.count_documents({"status": {"$ne": "closed"}}):
                raise EntryBlocked("One-position safeguard: existing position")
            if self.mode == "live":
                if not self.is_paper:
                    raise EntryBlocked("Loss protection supports paper entries only")
                positions = await self._req("GET", self.trading, "/positions")
                orders = await self._req("GET", self.trading, "/orders", params={"status": "open", "limit": 500})
                if not isinstance(positions, list) or not isinstance(orders, list) or positions or orders:
                    raise EntryBlocked("Broker positions/orders missing or not flat")
            await self.recompute_equity([])
            account = await self.get_account()
            cfg = await db.config.find_one({"id": "risk_config"}, {"_id": 0}) or RiskConfig().model_dump()
            await check_losses(db, account, cfg)
            from engines import risk_gate
            _, passed, _ = risk_gate(proposal, cfg, positive(account.get("equity")), [])
            if not passed:
                raise EntryBlocked("Fresh risk gate rejected entry")
            submitted = True
            result = await method(self, proposal, cycle_id=cycle_id)
            await db.entry_reservations.update_one({"_id": "single_position", "owner": owner},
                {"$set": {"order_id": result.get("order_id"), "status": result.get("status")}})
            # Only a confirmed zero-fill terminal cancellation/rejection is safe to retry.
            if (result.get("status") in {"unfilled", "canceled", "rejected", "expired"}
                    and result.get("alpaca_status", result.get("status")) in {"canceled", "rejected", "expired"}
                    and result.get("filled_qty") == 0):
                submitted = False
            return result
        finally:
            if not submitted:
                await db.entry_reservations.delete_one({"_id": "single_position", "owner": owner})
    return guarded

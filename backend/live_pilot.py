"""Petra Micro-Live: tiny, long-only fractional-equity funded execution pilot.

Purpose: collect real execution/fill/reconciliation evidence with deliberately tiny
notional exposure. This is NOT the options strategy and it never submits options,
short sales, leverage requests, or more than one managed position.

Default is shadow mode. Funded orders require two independent gates:
- PETRA_LIVE_PILOT_ARMED=true
- PETRA_LIVE_EXECUTION_CONFIRM=LIVE_PILOT
"""
from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from live_pilot_settings import (
    ALPACA_DATA_URL,
    LIVE_TRADING_URL,
    LivePilotSettingsError,
    load_live_pilot_settings,
)
from store import Lease, LeaseNotAcquired, StoreError, connect_store

ET = ZoneInfo("America/New_York")
log = logging.getLogger("petra.micro_live")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

TERMINAL = {"filled", "canceled", "rejected", "expired", "done_for_day"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LivePilotBroker:
    def __init__(self, settings, db):
        self.s = settings
        self.db = db
        self.trading = LIVE_TRADING_URL
        self.data = ALPACA_DATA_URL

    async def req(self, method: str, base: str, path: str, **kwargs):
        headers = {
            "APCA-API-KEY-ID": self.s.api_key,
            "APCA-API-SECRET-KEY": self.s.api_secret,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(headers=headers, timeout=20.0) as client:
            r = await client.request(method, base + path, **kwargs)
            if r.is_error:
                raise RuntimeError(f"Alpaca {method} {path} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}

    async def account(self):
        return await self.req("GET", self.trading, "/account")

    async def clock(self):
        return await self.req("GET", self.trading, "/clock")

    async def positions(self):
        return await self.req("GET", self.trading, "/positions")

    async def open_orders(self):
        return await self.req(
            "GET", self.trading, "/orders",
            params={"status": "open", "limit": 500, "direction": "desc"},
        )

    async def asset(self, symbol: str):
        return await self.req("GET", self.trading, f"/assets/{symbol}")

    async def snapshots(self, symbols):
        return await self.req(
            "GET", self.data, "/v2/stocks/snapshots",
            params={"symbols": ",".join(symbols), "feed": "iex"},
        )

    async def await_order(self, order_id: str, wait_s: int = 20):
        order = await self.req("GET", self.trading, f"/orders/{order_id}")
        if str(order.get("status") or "").lower() in TERMINAL:
            return order
        deadline = asyncio.get_running_loop().time() + wait_s
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1.0)
            order = await self.req("GET", self.trading, f"/orders/{order_id}")
            if str(order.get("status") or "").lower() in TERMINAL:
                return order

        # Market orders should not remain open. Re-check before cancel, then re-check after.
        order = await self.req("GET", self.trading, f"/orders/{order_id}")
        if str(order.get("status") or "").lower() in TERMINAL:
            return order
        try:
            await self.req("DELETE", self.trading, f"/orders/{order_id}")
        except RuntimeError:
            pass
        await asyncio.sleep(1.0)
        return await self.req("GET", self.trading, f"/orders/{order_id}")

    async def submit(self, payload: dict, intent: str):
        if not self.s.can_submit:
            raise LivePilotSettingsError("Funded submission gate is not armed")

        symbol = str(payload.get("symbol") or "")
        if symbol not in self.s.symbols:
            raise LivePilotSettingsError("Order symbol is outside the live-pilot allowlist")
        if payload.get("type") != "market" or payload.get("time_in_force") != "day":
            raise LivePilotSettingsError("Micro-live orders must be market/day")
        if payload.get("side") not in {"buy", "sell"}:
            raise LivePilotSettingsError("Micro-live side must be buy or sell")
        if intent == "entry":
            if payload.get("side") != "buy" or "qty" in payload:
                raise LivePilotSettingsError("Entries must be long-only dollar-notional buys")
            notional = float(payload.get("notional") or 0)
            if not 1.0 <= notional <= self.s.trade_notional_usd:
                raise LivePilotSettingsError("Entry notional exceeds the configured micro-live cap")
        elif intent == "exit":
            if payload.get("side") != "sell" or "notional" in payload:
                raise LivePilotSettingsError("Exits must sell the managed fractional quantity")
            if float(payload.get("qty") or 0) <= 0:
                raise LivePilotSettingsError("Exit quantity must be positive")
        else:
            raise LivePilotSettingsError("Unsupported micro-live order intent")

        raw = await self.req("POST", self.trading, "/orders", json=payload)
        final = await self.await_order(str(raw["id"]))
        status = str(final.get("status") or "unknown").lower()
        requested_qty = float(final.get("qty") or payload.get("qty") or 0)
        filled_qty = float(final.get("filled_qty") or 0)
        filled_price = float(final.get("filled_avg_price") or 0)
        result = {
            "order_id": str(final.get("id") or raw.get("id") or ""),
            "broker_status": status,
            "filled_qty": filled_qty,
            "requested_qty": requested_qty,
            "filled_price": filled_price,
            "client_order_id": str(final.get("client_order_id") or payload.get("client_order_id") or ""),
        }
        if status == "filled" and filled_qty > 0 and filled_price > 0:
            result["status"] = "filled"
        elif filled_qty > 0 or status == "partially_filled":
            result["status"] = "partial_review"
        elif status in TERMINAL:
            result["status"] = "unfilled"
        else:
            result["status"] = "review_required"

        await self.db.micro_orders.insert_one({
            "id": str(uuid.uuid4()),
            "ts": now_iso(),
            "intent": intent,
            "symbol": symbol,
            "notional": float(payload.get("notional") or 0),
            "qty": float(payload.get("qty") or 0),
            **result,
        })
        return result


def _snapshot_metrics(symbol: str, snap: dict):
    trade = snap.get("latestTrade") or {}
    daily = snap.get("dailyBar") or {}
    prev = snap.get("prevDailyBar") or {}
    price = float(trade.get("p") or daily.get("c") or 0)
    day_open = float(daily.get("o") or 0)
    prev_close = float(prev.get("c") or 0)
    if price <= 0 or day_open <= 0 or prev_close <= 0:
        return None
    return {
        "symbol": symbol,
        "price": price,
        "day_open": day_open,
        "prev_close": prev_close,
        "change_pct": (price / prev_close - 1) * 100,
        "from_open_pct": (price / day_open - 1) * 100,
    }


async def bind_account(db, account, expected: str):
    connected = str(account.get("account_number") or "")
    if not connected or connected != expected:
        raise StoreError("Connected funded Alpaca account does not match live-pilot whitelist")
    existing = await db.micro_account.find_one({"id": "account"})
    if existing and str(existing.get("account_number") or "") != connected:
        raise StoreError("Live-pilot Mongo database is bound to a different funded account")
    await db.micro_account.update_one(
        {"id": "account"},
        {"$set": {
            "id": "account",
            "account_number": connected,
            "status": account.get("status"),
            "cash": float(account.get("cash") or 0),
            "equity": float(account.get("equity") or 0),
            "updated_at": now_iso(),
        }},
        upsert=True,
    )


async def _realized_today(db):
    start = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    rows = await db.micro_positions.find(
        {"status": "closed", "closed_at": {"$gte": start.isoformat()}},
        {"_id": 0, "realized_pnl": 1},
    ).to_list(200)
    return round(sum(float(r.get("realized_pnl") or 0) for r in rows), 4)


async def _working_order_for_symbol(broker, symbol: str):
    for order in await broker.open_orders():
        if str(order.get("symbol") or "") == symbol:
            return order
    return None


async def manage_open_position(db, broker, settings, position, metrics, clock):
    symbol = position["symbol"]
    broker_positions = {str(p.get("symbol")): p for p in await broker.positions()}
    raw = broker_positions.get(symbol)
    if not raw:
        await db.micro_positions.update_one({"id": position["id"]}, {"$set": {
            "management_status": "review_required",
            "review_reason": "Managed position is absent from Alpaca broker positions",
            "updated_at": now_iso(),
        }})
        raise StoreError("Managed micro-live position is absent from Alpaca")

    managed_qty = float(position.get("qty") or 0)
    broker_qty = abs(float(raw.get("qty") or 0))
    if managed_qty <= 0 or broker_qty + 1e-9 < managed_qty:
        await db.micro_positions.update_one({"id": position["id"]}, {"$set": {
            "management_status": "review_required",
            "review_reason": "Broker quantity is smaller than Petra managed quantity",
            "updated_at": now_iso(),
        }})
        raise StoreError("Micro-live broker quantity mismatch")

    if await _working_order_for_symbol(broker, symbol):
        log.warning("Working broker order already exists for %s; no duplicate exit", symbol)
        return "working_order"

    entry = float(position["entry_price"])
    current = float(metrics["price"])
    return_pct = (current / entry - 1) * 100
    opened = datetime.fromisoformat(position["opened_at"])
    held_minutes = (datetime.now(timezone.utc) - opened).total_seconds() / 60

    next_close = datetime.fromisoformat(str(clock["next_close"]).replace("Z", "+00:00"))
    minutes_to_close = (next_close.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 60

    reason = None
    if return_pct <= -settings.stop_loss_pct:
        reason = "stop_loss"
    elif return_pct >= settings.take_profit_pct:
        reason = "take_profit"
    elif held_minutes >= settings.max_hold_minutes:
        reason = "max_hold"
    elif minutes_to_close <= 10:
        reason = "end_of_day"

    await db.micro_positions.update_one({"id": position["id"]}, {"$set": {
        "current_price": current,
        "unrealized_pnl": round((current - entry) * managed_qty, 4),
        "return_pct": round(return_pct, 4),
        "updated_at": now_iso(),
    }})

    if not reason:
        return "hold"
    if settings.dry_run:
        log.info("SHADOW would exit %s reason=%s return_pct=%.4f", symbol, reason, return_pct)
        return f"shadow_{reason}"

    payload = {
        "symbol": symbol,
        "qty": f"{managed_qty:.9f}".rstrip("0").rstrip("."),
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": f"petra-micro-exit-{settings.run_id[:18]}",
    }
    result = await broker.submit(payload, "exit")
    if result["status"] != "filled":
        await db.micro_positions.update_one({"id": position["id"]}, {"$set": {
            "management_status": "review_required" if result["status"] != "unfilled" else "managed",
            "review_reason": f"Exit order {result['broker_status']} filled {result['filled_qty']}/{result['requested_qty']}",
            "last_exit_order_id": result["order_id"],
            "updated_at": now_iso(),
        }})
        return result["status"]

    realized = round((result["filled_price"] - entry) * result["filled_qty"], 4)
    await db.micro_positions.update_one({"id": position["id"]}, {"$set": {
        "status": "closed",
        "exit_reason": reason,
        "exit_price": result["filled_price"],
        "exit_order_id": result["order_id"],
        "realized_pnl": realized,
        "closed_at": now_iso(),
        "updated_at": now_iso(),
    }})
    return f"closed_{reason}"


async def maybe_enter(db, broker, settings, snapshots, account, clock):
    # Existing broker holdings in a pilot symbol must never be mixed with a new Petra lot.
    broker_positions = {str(p.get("symbol")): p for p in await broker.positions()}
    managed_open = await db.micro_positions.find_one({"status": "open"}, {"_id": 0})
    if managed_open:
        return "position_exists"

    if await db.micro_positions.count_documents({"status": "open"}) > 0:
        return "position_exists"

    metrics = []
    for symbol in settings.symbols:
        if symbol in broker_positions:
            log.warning("Skipping %s: funded account already holds it outside the micro-live position ledger", symbol)
            continue
        asset = await broker.asset(symbol)
        if not asset.get("tradable") or not asset.get("fractionable"):
            continue
        m = _snapshot_metrics(symbol, snapshots.get(symbol) or {})
        if m:
            metrics.append(m)
    if not metrics:
        return "no_eligible_symbol"

    # Simple transparent momentum gate for the execution pilot; not presented as an edge.
    candidates = [
        m for m in metrics
        if m["change_pct"] >= settings.entry_momentum_pct
        and m["from_open_pct"] > 0
        and m["change_pct"] <= 1.50
    ]
    if not candidates:
        return "no_signal"
    chosen = max(candidates, key=lambda m: m["change_pct"])

    if await _working_order_for_symbol(broker, chosen["symbol"]):
        return "working_order"

    cash = float(account.get("cash") or 0)
    allocation_cap = cash * settings.max_allocation_pct / 100.0
    notional = round(min(settings.trade_notional_usd, allocation_cap), 2)
    if notional < 1.0:
        return "insufficient_cash_for_minimum"

    if settings.dry_run:
        await db.micro_decisions.insert_one({
            "id": str(uuid.uuid4()),
            "ts": now_iso(),
            "run_id": settings.run_id,
            "outcome": "shadow_entry",
            "symbol": chosen["symbol"],
            "price": chosen["price"],
            "notional": notional,
            "change_pct": round(chosen["change_pct"], 4),
            "from_open_pct": round(chosen["from_open_pct"], 4),
        })
        log.info("SHADOW would buy $%.2f of %s", notional, chosen["symbol"])
        return "shadow_entry"

    payload = {
        "symbol": chosen["symbol"],
        "notional": f"{notional:.2f}",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": f"petra-micro-entry-{settings.run_id[:17]}",
    }
    result = await broker.submit(payload, "entry")
    if result["status"] != "filled":
        return result["status"]

    await db.micro_positions.insert_one({
        "id": str(uuid.uuid4()),
        "symbol": chosen["symbol"],
        "status": "open",
        "management_status": "managed",
        "qty": result["filled_qty"],
        "entry_price": result["filled_price"],
        "entry_notional": round(result["filled_qty"] * result["filled_price"], 4),
        "entry_order_id": result["order_id"],
        "opened_at": now_iso(),
        "current_price": result["filled_price"],
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "signal": {
            "type": "simple_momentum_execution_pilot",
            "change_pct": round(chosen["change_pct"], 4),
            "from_open_pct": round(chosen["from_open_pct"], 4),
        },
    })
    return "entry_filled"


async def main() -> int:
    settings = None
    store = None
    try:
        settings = load_live_pilot_settings()
        store = await connect_store(settings.mongo_url, settings.db_name)
        db = store.db
        await db.micro_runs.create_index("run_id", unique=True)
        await db.micro_positions.create_index("id", unique=True)
        await db.micro_positions.create_index([("status", 1), ("symbol", 1)])

        async with Lease(store, owner=f"micro-{settings.run_id}", ttl_seconds=900):
            broker = LivePilotBroker(settings, db)
            account = await broker.account()
            await bind_account(db, account, settings.expected_account_number)

            if account.get("account_blocked") or account.get("trading_blocked") or account.get("trade_suspended_by_user"):
                raise StoreError("Funded Alpaca account reports trading blocked/suspended")

            clock = await broker.clock()
            run = {
                "run_id": settings.run_id,
                "ts": now_iso(),
                "dry_run": settings.dry_run,
                "armed": settings.armed,
                "can_submit": settings.can_submit,
                "market_open": bool(clock.get("is_open")),
                "result": None,
            }

            open_pos = await db.micro_positions.find_one({"status": "open"}, {"_id": 0})
            snapshots = await broker.snapshots(settings.symbols)

            realized = await _realized_today(db)
            unrealized = 0.0
            if open_pos:
                m = _snapshot_metrics(open_pos["symbol"], snapshots.get(open_pos["symbol"]) or {})
                if not m:
                    raise StoreError("No live IEX snapshot for managed micro-live position")
                unrealized = round((m["price"] - float(open_pos["entry_price"])) * float(open_pos["qty"]), 4)

            day_pnl = round(realized + unrealized, 4)
            run["day_pnl"] = day_pnl

            # Existing risk may still be exited even after the daily-loss stop. The stop only blocks entries.
            if open_pos:
                if open_pos.get("management_status", "managed") != "managed":
                    run["result"] = "review_required"
                elif not clock.get("is_open"):
                    run["result"] = "market_closed_hold"
                else:
                    metrics = _snapshot_metrics(open_pos["symbol"], snapshots.get(open_pos["symbol"]) or {})
                    run["result"] = await manage_open_position(db, broker, settings, open_pos, metrics, clock)
            else:
                if day_pnl <= -settings.daily_loss_usd:
                    run["result"] = "daily_loss_stop"
                elif not clock.get("is_open"):
                    run["result"] = "market_closed"
                else:
                    # Do not enter in the first 15 minutes or final 30 minutes of the regular session.
                    now = datetime.now(timezone.utc)
                    next_close = datetime.fromisoformat(str(clock["next_close"]).replace("Z", "+00:00")).astimezone(timezone.utc)
                    minutes_to_close = (next_close - now).total_seconds() / 60
                    timestamp = datetime.fromisoformat(str(clock["timestamp"]).replace("Z", "+00:00")).astimezone(ET)
                    minutes_from_open = (timestamp.hour * 60 + timestamp.minute) - (9 * 60 + 30)
                    if minutes_from_open < 15:
                        run["result"] = "opening_buffer"
                    elif minutes_to_close <= 30:
                        run["result"] = "closing_buffer"
                    else:
                        run["result"] = await maybe_enter(db, broker, settings, snapshots, account, clock)

            await db.micro_runs.update_one({"run_id": settings.run_id}, {"$set": run}, upsert=True)
            log.info(
                "MICRO_LIVE_RESULT result=%s dry_run=%s armed=%s day_pnl=%.4f",
                run["result"], settings.dry_run, settings.armed, day_pnl,
            )
            return 0

    except LeaseNotAcquired:
        log.info("Another Petra micro-live worker owns the lease; no action")
        return 0
    except (LivePilotSettingsError, StoreError) as exc:
        log.error("Micro-live failed closed: %s", exc)
        return 1
    except Exception:
        log.exception("Unexpected micro-live failure; no further action")
        return 1
    finally:
        if store:
            await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

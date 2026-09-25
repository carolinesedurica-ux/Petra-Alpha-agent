"""One-time safe reconciliation of existing Alpaca PAPER positions into MongoDB.

This script never submits, replaces, or cancels an order. Imported positions are marked
management_status=review_required so the autonomous worker cannot close them or open new risk
until an operator explicitly reviews/promotes them.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from models import Position, new_id, now_iso
from settings import SettingsError, install_legacy_env, load_settings
from store import Lease, StoreError, connect_store

log = logging.getLogger("petra.reconcile")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ET = ZoneInfo("America/New_York")
OCC_RE = re.compile(r"^([A-Z0-9.]+?)(\d{6})([CP])(\d{8})$")


def parse_occ(symbol: str):
    m = OCC_RE.match(symbol or "")
    if not m:
        return None
    root, yymmdd, cp, strike_raw = m.groups()
    year = 2000 + int(yymmdd[:2])
    month = int(yymmdd[2:4])
    day = int(yymmdd[4:6])
    expiry = datetime(year, month, day, 16, 0, tzinfo=ET).astimezone(timezone.utc)
    return {
        "symbol": symbol,
        "underlying": root,
        "expiry_ts": expiry.isoformat(),
        "option_type": "call" if cp == "C" else "put",
        "strike": int(strike_raw) / 1000.0,
    }


def side_of(position: dict) -> str:
    side = str(position.get("side") or "").lower()
    if side in {"long", "buy"}:
        return "buy"
    if side in {"short", "sell"}:
        return "sell"
    try:
        return "sell" if float(position.get("qty") or 0) < 0 else "buy"
    except Exception:
        return "buy"


def qty_of(position: dict) -> int | None:
    try:
        q = abs(float(position.get("qty") or 0))
    except Exception:
        return None
    if q <= 0 or abs(q - round(q)) > 1e-9:
        return None
    return int(round(q))


def candidate_from_positions(rows: list[dict], matched_order: dict | None = None):
    parsed = []
    for row in rows:
        occ = parse_occ(str(row.get("symbol") or ""))
        qty = qty_of(row)
        if not occ or not qty:
            return None, "unparseable option symbol or quantity"
        parsed.append({
            **occ,
            "side": side_of(row),
            "contracts": qty,
            "avg_entry_price": float(row.get("avg_entry_price") or 0),
            "current_price": float(row.get("current_price") or 0),
            "unrealized_pl": float(row.get("unrealized_pl") or 0),
        })

    roots = {x["underlying"] for x in parsed}
    expiries = {x["expiry_ts"] for x in parsed}
    qtys = {x["contracts"] for x in parsed}
    if len(roots) != 1 or len(expiries) != 1 or len(qtys) != 1:
        return None, "legs do not share one underlying, expiry and quantity"

    puts = [x for x in parsed if x["option_type"] == "put"]
    calls = [x for x in parsed if x["option_type"] == "call"]

    def pair_kind(legs, kind):
        if len(legs) != 2:
            return False
        shorts = [x for x in legs if x["side"] == "sell"]
        longs = [x for x in legs if x["side"] == "buy"]
        if len(shorts) != 1 or len(longs) != 1:
            return False
        if kind == "put":
            return shorts[0]["strike"] > longs[0]["strike"]
        return shorts[0]["strike"] < longs[0]["strike"]

    if len(parsed) == 2 and pair_kind(puts, "put"):
        strategy = "put_credit_spread"
    elif len(parsed) == 2 and pair_kind(calls, "call"):
        strategy = "call_credit_spread"
    elif len(parsed) == 4 and pair_kind(puts, "put") and pair_kind(calls, "call"):
        strategy = "iron_condor"
    else:
        return None, "leg geometry is not a supported defined-risk credit structure"

    credit = round(
        sum(x["avg_entry_price"] if x["side"] == "sell" else -x["avg_entry_price"] for x in parsed),
        4,
    )
    if credit <= 0:
        return None, "broker average entry prices do not reconstruct a positive net credit"

    widths = []
    for kind_legs in (puts, calls):
        if not kind_legs:
            continue
        short = next(x for x in kind_legs if x["side"] == "sell")
        long = next(x for x in kind_legs if x["side"] == "buy")
        widths.append(abs(short["strike"] - long["strike"]))
    width = round(max(widths), 4)
    if width <= 0 or credit >= width:
        return None, "reconstructed credit/width is not plausible"

    contracts = parsed[0]["contracts"]
    max_risk = round((width - credit) * 100 * contracts, 2)
    expiry_ts = parsed[0]["expiry_ts"]
    dte = max(0.0, (datetime.fromisoformat(expiry_ts) - datetime.now(timezone.utc)).total_seconds() / 86400)
    current_value = round(max(0.0, sum(
        x["current_price"] if x["side"] == "sell" else -x["current_price"] for x in parsed
    )), 4)
    unrealized = round(sum(x["unrealized_pl"] for x in parsed), 2)

    order_id = ""
    opened_at = now_iso()
    order_match = False
    if matched_order:
        order_id = str(matched_order.get("id") or "")
        opened_at = str(matched_order.get("filled_at") or matched_order.get("created_at") or opened_at)
        order_match = True

    legs = [{
        "symbol": x["symbol"],
        "side": x["side"],
        "option_type": x["option_type"],
        "strike": x["strike"],
        "delta": 0.0,
        "price": x["avg_entry_price"],
    } for x in sorted(parsed, key=lambda z: (z["option_type"], z["strike"]))]

    pos = Position(
        underlying=parsed[0]["underlying"],
        strategy=strategy,
        legs=legs,
        contracts=contracts,
        width=width,
        credit=credit,
        max_risk=max_risk,
        entry_underlying=0.0,
        entry_iv=0.0,
        dte=round(dte, 2),
        expiry_ts=expiry_ts,
        tp_target=round(credit * 0.5, 4),
        stop_target=round(credit * 2.0, 4),
        current_value=current_value,
        unrealized_pnl=unrealized,
        unrealized_pct=round((unrealized / (credit * 100 * contracts)) * 100, 2) if credit else 0.0,
        risk_gate_score=0,
        alpaca_order_id=order_id,
        status="open",
        paper_sim=False,
        management_status="review_required",
        source="alpaca_reconciliation",
        reconciliation_notes=(
            "Imported from Alpaca paper broker state. "
            + ("Matched to a recent filled multi-leg order; " if order_match else "No exact recent filled order match; ")
            + "entry underlying/IV and deltas are not reconstructed. Automated exits and new entries remain blocked."
        ),
        opened_at=opened_at,
    )
    return pos, None


def order_symbols(order: dict) -> set[str]:
    return {str(x.get("symbol")) for x in (order.get("legs") or []) if x.get("symbol")}


async def main() -> int:
    settings = None
    store = None
    try:
        settings = load_settings()
        install_legacy_env(settings)
        from alpaca import LiveAlpaca

        store = await connect_store(settings.mongo_url, settings.db_name)
        db = store.db

        async with Lease(store, owner=f"reconcile-{settings.run_id}", ttl_seconds=settings.lease_ttl_seconds):
            broker = LiveAlpaca(db)
            if not broker.is_paper or broker.live_trading_armed:
                raise SettingsError("Reconciliation is paper-only and funded trading must remain disarmed")

            account = await broker._req("GET", broker.trading, "/account")
            await store.assert_account_binding(account, settings.expected_account_number)

            positions = await broker._req("GET", broker.trading, "/positions")
            open_orders = await broker._req(
                "GET", broker.trading, "/orders",
                params={"status": "open", "limit": 500, "nested": "true"},
            )
            recent_orders = await broker._req(
                "GET", broker.trading, "/orders",
                params={"status": "all", "limit": 500, "nested": "true", "direction": "desc"},
            )

            option_rows = [p for p in positions if p.get("asset_class") == "us_option"]
            non_option_rows = [p for p in positions if p.get("asset_class") != "us_option"]
            current_by_symbol = {str(p.get("symbol")): p for p in option_rows}

            filled_multileg = [
                o for o in recent_orders
                if str(o.get("status") or "").lower() == "filled" and len(order_symbols(o)) >= 2
            ]

            groups: list[tuple[list[dict], dict | None, str]] = []
            used: set[str] = set()

            # Highest confidence: exact current-leg set from a recent filled multi-leg order.
            for order in filled_multileg:
                syms = order_symbols(order)
                if syms and syms.issubset(current_by_symbol) and not (syms & used):
                    rows = [current_by_symbol[s] for s in syms]
                    groups.append((rows, order, "filled_order_match"))
                    used |= syms

            # Conservative fallback: only exact 2/4-leg groups sharing underlying/expiry/qty.
            buckets = defaultdict(list)
            for row in option_rows:
                sym = str(row.get("symbol") or "")
                if sym in used:
                    continue
                occ = parse_occ(sym)
                qty = qty_of(row)
                if occ and qty:
                    buckets[(occ["underlying"], occ["expiry_ts"], qty)].append(row)

            for rows in buckets.values():
                if len(rows) in (2, 4):
                    groups.append((rows, None, "geometry_match"))
                    used |= {str(r.get("symbol")) for r in rows}

            imported = []
            review = []
            for rows, order, method in groups:
                pos, error = candidate_from_positions(rows, order)
                symbols = sorted(str(r.get("symbol") or "") for r in rows)
                if error:
                    review.append({"symbols": symbols, "reason": error, "method": method})
                    continue

                existing = await db.positions.find_one({
                    "status": "open",
                    "source": "alpaca_reconciliation",
                    "legs.symbol": {"$all": symbols},
                })
                if existing:
                    imported.append({"position_id": existing["id"], "strategy": existing["strategy"], "existing": True})
                    continue

                doc = pos.model_dump()
                doc["reconciliation_method"] = method
                await db.positions.insert_one(doc)
                imported.append({"position_id": pos.id, "strategy": pos.strategy, "existing": False})

            unmatched_symbols = sorted(set(current_by_symbol) - used)
            for sym in unmatched_symbols:
                review.append({"symbols": [sym], "reason": "not safely groupable", "method": "unmatched"})

            run_doc = {
                "id": new_id(),
                "ts": now_iso(),
                "mode": "paper",
                "option_leg_count": len(option_rows),
                "non_option_position_count": len(non_option_rows),
                "open_order_count": len(open_orders),
                "imported_position_count": len(imported),
                "review_item_count": len(review),
                "imported": imported,
                "review": review,
                "status": "review_required" if (review or non_option_rows or open_orders) else "imported_review_required",
            }
            await db.reconciliation_runs.insert_one(run_doc)

            # Store sanitized broker inventory for later audit; never credentials/account number.
            await db.broker_inventory.replace_one(
                {"id": "latest"},
                {
                    "id": "latest",
                    "ts": now_iso(),
                    "option_positions": [{
                        "symbol": p.get("symbol"),
                        "qty": p.get("qty"),
                        "side": p.get("side"),
                        "avg_entry_price": p.get("avg_entry_price"),
                        "current_price": p.get("current_price"),
                        "unrealized_pl": p.get("unrealized_pl"),
                    } for p in option_rows],
                    "non_option_positions": [{
                        "symbol": p.get("symbol"),
                        "asset_class": p.get("asset_class"),
                        "qty": p.get("qty"),
                        "side": p.get("side"),
                    } for p in non_option_rows],
                    "open_orders": [{
                        "id": o.get("id"),
                        "client_order_id": o.get("client_order_id"),
                        "status": o.get("status"),
                        "order_class": o.get("order_class"),
                        "symbols": sorted(order_symbols(o)),
                    } for o in open_orders],
                },
                upsert=True,
            )

            log.info(
                "Reconciliation inventory complete: option_legs=%d imported_positions=%d review_items=%d "
                "non_option_positions=%d open_orders=%d",
                len(option_rows), len(imported), len(review), len(non_option_rows), len(open_orders),
            )
            return 0

    except (SettingsError, StoreError) as exc:
        log.error("Reconciliation failed closed: %s", type(exc).__name__)
        return 1
    except Exception:
        log.exception("Unexpected reconciliation failure")
        return 1
    finally:
        if store:
            await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

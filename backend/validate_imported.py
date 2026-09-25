"""Read-only validation of imported Alpaca PAPER positions.

No order mutation. No management promotion. Produces a sanitized validation record/log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

from settings import SettingsError, install_legacy_env, load_settings
from store import Lease, StoreError, connect_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("petra.validate")


def f(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def order_symbols(order):
    return {str(x.get("symbol")) for x in (order.get("legs") or []) if x.get("symbol")}


async def main():
    settings = None
    store = None
    try:
        settings = load_settings()
        install_legacy_env(settings)
        from alpaca import LiveAlpaca

        store = await connect_store(settings.mongo_url, settings.db_name)
        db = store.db
        async with Lease(store, owner=f"validate-{settings.run_id}", ttl_seconds=settings.lease_ttl_seconds):
            broker = LiveAlpaca(db)
            if not broker.is_paper or broker.live_trading_armed:
                raise SettingsError("Validation is paper-only and funded trading must remain disarmed")

            account = await broker._req("GET", broker.trading, "/account")
            await store.assert_account_binding(account, settings.expected_account_number)

            imported = await db.positions.find(
                {"status": "open", "source": "alpaca_reconciliation", "management_status": "review_required"},
                {"_id": 0},
            ).to_list(50)
            if not imported:
                log.error("No imported review_required positions found")
                return 2

            broker_positions = await broker._req("GET", broker.trading, "/positions")
            open_orders = await broker._req(
                "GET", broker.trading, "/orders",
                params={"status": "open", "limit": 500, "nested": "true"},
            )
            recent_orders = await broker._req(
                "GET", broker.trading, "/orders",
                params={"status": "all", "limit": 500, "nested": "true", "direction": "desc"},
            )
            by_symbol = {str(p.get("symbol")): p for p in broker_positions}

            all_ok = True
            reports = []
            for p in imported:
                symbols = [str(x.get("symbol")) for x in p.get("legs", [])]
                symset = set(symbols)
                checks = []

                exact_rows = [by_symbol.get(s) for s in symbols]
                check = all(exact_rows)
                checks.append(("all_legs_present_at_broker", check))
                all_ok &= check

                expected_qty = int(p.get("contracts") or 0)
                qty_ok = all(abs(f(r.get("qty"))) == expected_qty for r in exact_rows if r)
                checks.append(("quantities_match", qty_ok))
                all_ok &= qty_ok

                side_ok = True
                for leg in p.get("legs", []):
                    row = by_symbol.get(str(leg.get("symbol")))
                    if not row:
                        side_ok = False
                        continue
                    raw_side = str(row.get("side") or "").lower()
                    broker_side = "sell" if raw_side == "short" or f(row.get("qty")) < 0 else "buy"
                    if broker_side != leg.get("side"):
                        side_ok = False
                checks.append(("sides_match", side_ok))
                all_ok &= side_ok

                working = [o for o in open_orders if order_symbols(o) & symset]
                no_working = len(working) == 0
                checks.append(("no_open_orders_on_legs", no_working))
                all_ok &= no_working

                reconstructed_credit = round(sum(
                    f(by_symbol[s].get("avg_entry_price")) if leg.get("side") == "sell"
                    else -f(by_symbol[s].get("avg_entry_price"))
                    for leg in p.get("legs", [])
                    for s in [str(leg.get("symbol"))]
                    if s in by_symbol
                ), 4)
                stored_credit = round(f(p.get("credit")), 4)
                credit_ok = stored_credit > 0 and abs(reconstructed_credit - stored_credit) <= 0.01
                checks.append(("credit_reconstructs", credit_ok))
                all_ok &= credit_ok

                width = f(p.get("width"))
                contracts = expected_qty
                expected_risk = round(max(0.0, width - stored_credit) * 100 * contracts, 2)
                stored_risk = round(f(p.get("max_risk")), 2)
                risk_ok = expected_risk > 0 and abs(expected_risk - stored_risk) <= 1.0
                checks.append(("max_risk_reconstructs", risk_ok))
                all_ok &= risk_ok

                exact_filled = [
                    o for o in recent_orders
                    if str(o.get("status") or "").lower() == "filled"
                    and order_symbols(o) == symset
                ]
                order_match = exact_filled[0] if exact_filled else None
                order_ok = order_match is not None
                checks.append(("exact_filled_mleg_history_found", order_ok))
                all_ok &= order_ok

                filled_avg = abs(f((order_match or {}).get("filled_avg_price")))
                # For Alpaca mleg credit orders, filled_avg_price may be signed/absolute depending on response.
                # Treat it as corroborating only when populated; do not fail solely if omitted.
                fill_price_corrob = None
                if filled_avg > 0:
                    fill_price_corrob = abs(filled_avg - stored_credit) <= 0.05

                marks = await broker.close_values([p], {})
                marked_value, remain = marks[p["id"]]
                mark_ok = marked_value >= 0 and remain >= 0
                checks.append(("live_close_mark_available", mark_ok))
                all_ok &= mark_ok

                tp = round(stored_credit * 0.5, 4)
                stop = round(stored_credit * 2.0, 4)
                exit_state = (
                    "take_profit" if marked_value <= tp
                    else "stop_loss" if marked_value >= stop
                    else "time_exit" if remain <= 0.75
                    else "hold"
                )

                report = {
                    "position_id": p.get("id"),
                    "underlying": p.get("underlying"),
                    "strategy": p.get("strategy"),
                    "contracts": contracts,
                    "leg_symbols": symbols,
                    "stored_credit": stored_credit,
                    "reconstructed_credit": reconstructed_credit,
                    "width": width,
                    "stored_max_risk": stored_risk,
                    "reconstructed_max_risk": expected_risk,
                    "matched_filled_order": bool(order_match),
                    "filled_avg_price": filled_avg if filled_avg else None,
                    "filled_avg_price_corrob": fill_price_corrob,
                    "current_close_value": round(marked_value, 4),
                    "take_profit_threshold": tp,
                    "stop_threshold": stop,
                    "dte_remaining": round(remain, 3),
                    "current_exit_state": exit_state,
                    "checks": [{"name": k, "passed": v} for k, v in checks],
                    "validated": all(v for _, v in checks),
                }
                reports.append(report)

                await db.position_validation_runs.insert_one({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    **report,
                })
                await db.positions.update_one(
                    {"id": p["id"]},
                    {"$set": {
                        "validation_status": "validated" if report["validated"] else "review_required",
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                        "validation_summary": {
                            "stored_credit": stored_credit,
                            "reconstructed_credit": reconstructed_credit,
                            "stored_max_risk": stored_risk,
                            "reconstructed_max_risk": expected_risk,
                            "matched_filled_order": bool(order_match),
                            "current_close_value": round(marked_value, 4),
                            "dte_remaining": round(remain, 3),
                            "current_exit_state": exit_state,
                        },
                    }},
                )

            log.info("VALIDATION_REPORT %s", json.dumps(reports, separators=(",", ":")))
            return 0 if all_ok else 2

    except (SettingsError, StoreError) as exc:
        log.error("Validation failed closed: %s", type(exc).__name__)
        return 1
    except Exception:
        log.exception("Unexpected validation failure")
        return 1
    finally:
        if store:
            await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

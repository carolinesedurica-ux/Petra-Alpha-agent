"""Promote validated imported Alpaca PAPER positions to managed, with broker re-check.

This script never submits/cancels/replaces an order.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from settings import SettingsError, install_legacy_env, load_settings
from store import Lease, StoreError, connect_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("petra.promote")


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

        async with Lease(store, owner=f"promote-{settings.run_id}", ttl_seconds=settings.lease_ttl_seconds):
            broker = LiveAlpaca(db)
            if not broker.is_paper or broker.live_trading_armed:
                raise SettingsError("Promotion is paper-only and funded trading must remain disarmed")

            account = await broker._req("GET", broker.trading, "/account")
            await store.assert_account_binding(account, settings.expected_account_number)

            rows = await db.positions.find({
                "status": "open",
                "source": "alpaca_reconciliation",
                "management_status": "review_required",
                "validation_status": "validated",
            }, {"_id": 0}).to_list(50)
            if not rows:
                log.error("No validated imported positions are eligible for promotion")
                return 2

            broker_positions = await broker._req("GET", broker.trading, "/positions")
            open_orders = await broker._req(
                "GET", broker.trading, "/orders",
                params={"status": "open", "limit": 500, "nested": "true"},
            )
            by_symbol = {str(p.get("symbol")): p for p in broker_positions}

            promoted = 0
            for p in rows:
                symbols = [str(x.get("symbol")) for x in p.get("legs", [])]
                symbol_set = set(symbols)
                expected_qty = int(p.get("contracts") or 0)

                if not all(s in by_symbol for s in symbols):
                    raise StoreError(f"Broker no longer holds every validated leg for position {p['id']}")

                for leg in p.get("legs", []):
                    sym = str(leg.get("symbol"))
                    row = by_symbol[sym]
                    qty = abs(f(row.get("qty")))
                    if abs(qty - expected_qty) > 1e-9:
                        raise StoreError(f"Broker quantity changed for validated position {p['id']}")
                    raw_side = str(row.get("side") or "").lower()
                    broker_side = "sell" if raw_side == "short" or f(row.get("qty")) < 0 else "buy"
                    if broker_side != leg.get("side"):
                        raise StoreError(f"Broker side changed for validated position {p['id']}")

                touching_orders = [o for o in open_orders if order_symbols(o) & symbol_set]
                if touching_orders:
                    raise StoreError(f"Working broker order touches validated position {p['id']}")

                # Verify live close marking still works immediately before promotion.
                marks = await broker.close_values([p], {})
                close_value, dte = marks[p["id"]]
                if close_value < 0 or dte < 0:
                    raise StoreError(f"Live close mark invalid for position {p['id']}")

                await db.positions.update_one({"id": p["id"]}, {"$set": {
                    "management_status": "managed",
                    "managed_at": datetime.now(timezone.utc).isoformat(),
                    "reconcile_warned": False,
                    "reconciliation_notes": (
                        "Imported Alpaca paper position validated against broker legs, quantities, "
                        "filled multi-leg history, entry credit, max risk, and live close quotes; "
                        "approved for exits-only management."
                    ),
                }})
                promoted += 1

            log.info("PROMOTION_COMPLETE promoted_positions=%d entries_enabled=false funded=false", promoted)
            return 0

    except (SettingsError, StoreError) as exc:
        log.error("Promotion failed closed: %s", exc)
        return 2
    except Exception:
        log.exception("Unexpected promotion failure")
        return 1
    finally:
        if store:
            await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""Configure the existing Alpaca PAPER database for a conservative entry pilot.

Paper only. No order is submitted by this script.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from settings import SettingsError, install_legacy_env, load_settings
from store import Lease, StoreError, connect_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("petra.paper_entry_config")


async def main() -> int:
    settings = None
    store = None
    try:
        settings = load_settings()
        install_legacy_env(settings)
        from alpaca import LiveAlpaca
        from agent import get_config

        store = await connect_store(settings.mongo_url, settings.db_name)
        db = store.db
        async with Lease(store, owner=f"paper-entry-config-{settings.run_id}", ttl_seconds=settings.lease_ttl_seconds):
            broker = LiveAlpaca(db)
            if not broker.is_paper or broker.live_trading_armed:
                raise SettingsError("Paper entry pilot requires the exact Alpaca paper endpoint and funded trading locked")

            account = await broker._req("GET", broker.trading, "/account")
            await store.assert_account_binding(account, settings.expected_account_number)

            review_count = await db.positions.count_documents({
                "status": "open",
                "management_status": {"$ne": "managed"},
            })
            if review_count:
                raise StoreError("Open positions still require reconciliation review; paper entries remain blocked")

            cfg = await get_config(db)
            await db.config.update_one({"id": "risk_config"}, {"$set": {
                "max_contracts": 1,
                "max_concurrent": 2,
                "max_risk_pct": min(float(cfg.get("max_risk_pct", 2.0)), 2.0),
                "max_daily_loss_pct": min(float(cfg.get("max_daily_loss_pct", 2.0)), 2.0),
            }}, upsert=True)

            log.info("PAPER_ENTRY_CONFIG max_contracts=1 max_concurrent=2 max_candidates=1")
            return 0
    except (SettingsError, StoreError) as exc:
        log.error("Paper-entry configuration failed closed: %s", exc)
        return 1
    except Exception:
        log.exception("Unexpected paper-entry configuration failure")
        return 1
    finally:
        if store:
            await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

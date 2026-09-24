"""One-shot Petra paper-trading worker.

Exit 0: healthy run/no-op.
Exit 1: failed closed due to configuration/infrastructure/unexpected error.
Exit 2: broker/database state needs human review; no new risk was opened.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

from settings import SettingsError, install_legacy_env, load_settings
from store import Lease, LeaseNotAcquired, StoreError, connect_store


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("petra.worker")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


async def _start_run(db, run_id: str, settings):
    doc = {
        "run_id": run_id,
        "started_at": now_iso(),
        "finished_at": None,
        "phase": "start",
        "exit_code": None,
        "dry_run": settings.dry_run,
        "entries_enabled": settings.entries_enabled,
        "broker_env": "paper",
    }
    await db.worker_runs.update_one({"run_id": run_id}, {"$set": doc}, upsert=True)


async def _finish_run(db, run_id: str, code: int, phase: str, detail: str = ""):
    await db.worker_runs.update_one({"run_id": run_id}, {"$set": {
        "finished_at": now_iso(),
        "phase": phase,
        "exit_code": code,
        "detail": detail[:300],
    }}, upsert=True)


async def main() -> int:
    settings = None
    store = None
    code = 1
    run_id = "unconfigured"

    try:
        settings = load_settings()
        run_id = settings.run_id
        install_legacy_env(settings)

        # Import after validated paper-only environment has been installed.
        from alpaca import LiveAlpaca
        from agent import manage_positions, mark_positions, run_cycle

        store = await connect_store(settings.mongo_url, settings.db_name)
        db = store.db
        await _start_run(db, run_id, settings)

        async with Lease(store, owner=run_id, ttl_seconds=settings.lease_ttl_seconds) as lease:
            broker = LiveAlpaca(db)
            if not broker.is_paper or broker.trading != "https://paper-api.alpaca.markets/v2":
                raise SettingsError("Worker broker is not connected to the exact Alpaca paper endpoint")
            if broker.live_trading_armed:
                raise SettingsError("Funded trading interlock is unexpectedly armed")

            raw_account = await broker._req("GET", broker.trading, "/account")
            await store.assert_account_binding(raw_account, settings.expected_account_number)

            # Safe now: ensure_seed cannot delete or replace a mismatched database.
            await broker.ensure_seed()
            await lease.assert_owned()

            is_open = await broker.market_open()
            await db.worker_runs.update_one({"run_id": run_id}, {"$set": {
                "phase": "validated",
                "market_open": bool(is_open),
            }})

            # DRY RUN: refresh/reconcile/mark state, but never submit any order.
            if settings.dry_run:
                try:
                    open_pos, _ = await mark_positions(db, broker)
                    detail = f"shadow mode; market_open={bool(is_open)}; managed_positions={len(open_pos)}"
                    await _finish_run(db, run_id, 0, "shadow_complete", detail)
                    log.info("Shadow run completed; no orders permitted")
                    return 0
                except Exception as exc:
                    await _finish_run(db, run_id, 2, "shadow_needs_review", type(exc).__name__)
                    log.error("Shadow reconciliation needs review: %s", type(exc).__name__)
                    return 2

            # With entries disabled, Petra may manage existing positions but cannot open new risk.
            if not settings.entries_enabled:
                if is_open:
                    cfg_doc = await db.config.find_one({"id": "risk_config"}, {"_id": 0})
                    if not cfg_doc:
                        from agent import get_config
                        cfg_doc = await get_config(db)
                    await manage_positions(db, broker, cfg_doc, run_id)
                else:
                    await mark_positions(db, broker)
                await _finish_run(db, run_id, 0, "exits_only_complete",
                                  f"market_open={bool(is_open)}; entries disabled")
                log.info("Exits-only run completed; new entries disabled")
                return 0

            # New entries require BOTH dry_run=false and entries_enabled=true.
            if not is_open:
                await _finish_run(db, run_id, 0, "market_closed", "Alpaca clock reports market closed")
                log.info("Market closed; no orders submitted")
                return 0

            await lease.assert_owned()
            result = await run_cycle(
                db,
                broker,
                force=False,
                max_candidates=settings.max_candidates,
            )
            await _finish_run(db, run_id, 0, "cycle_complete", str(result.get("status", "ran")))
            log.info("Paper cycle completed with status=%s", result.get("status"))
            return 0

    except LeaseNotAcquired:
        # Safe no-op: another executor is already active.
        if store and settings:
            await _finish_run(store.db, run_id, 0, "lease_held", "another worker owns the lease")
        log.info("Another Petra worker is active; exiting without trading")
        return 0
    except (SettingsError, StoreError) as exc:
        if store and settings:
            await _finish_run(store.db, run_id, 1, "failed_closed", type(exc).__name__)
        log.error("Failed closed: %s", type(exc).__name__)
        return 1
    except Exception as exc:
        if store and settings:
            try:
                await _finish_run(store.db, run_id, 1, "unexpected_failure", type(exc).__name__)
            except Exception:
                pass
        log.exception("Unexpected worker failure; no further risk will be opened")
        return 1
    finally:
        if store:
            await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

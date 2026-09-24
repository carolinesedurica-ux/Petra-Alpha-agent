"""Persistent store and executor lease for Petra's one-shot worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class StoreError(RuntimeError):
    pass


class LeaseNotAcquired(StoreError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


class WorkerStore:
    def __init__(self, client: AsyncIOMotorClient, db):
        self.client = client
        self.db = db

    async def ensure_indexes(self):
        await self.db.worker_runs.create_index("run_id", unique=True)
        await self.db.order_intents.create_index("client_order_id", unique=True)
        await self.db.positions.create_index("id", unique=True, sparse=True)
        await self.db.decisions.create_index("id", unique=True, sparse=True)
        await self.db.pnl_snapshots.create_index("ts")

    async def assert_account_binding(self, account: dict, expected_account_number: str):
        connected = str(account.get("account_number") or "")
        if not connected:
            raise StoreError("Alpaca account response did not include an account number")
        if connected != expected_account_number:
            raise StoreError("Connected Alpaca paper account does not match the configured account")

        bound = await self.db.account.find_one({"id": "account"})
        if not bound:
            if await self.db.positions.count_documents({"status": {"$in": ["open", "closing", "pending_entry"]}}):
                raise StoreError(
                    "Open trading state exists but the account binding is missing; refusing to guess"
                )
            await self.db.account.insert_one({
                "id": "account",
                "mode": "live",
                "broker_env": "paper",
                "account_number": connected,
                "initial_equity": float(account.get("equity") or 0),
                "equity": float(account.get("equity") or 0),
                "cash": float(account.get("cash") or 0),
                "buying_power": float(account.get("options_buying_power") or account.get("buying_power") or 0),
                "day_start_equity": float(account.get("last_equity") or account.get("equity") or 0),
                "bound_at": utcnow().isoformat(),
                "updated_at": utcnow().isoformat(),
            })
            return

        if bound.get("mode") != "live" or bound.get("broker_env") not in (None, "paper"):
            raise StoreError(
                "Database contains mock/non-paper account state. Migration is required; worker will not overwrite it."
            )
        if str(bound.get("account_number") or "") != connected:
            raise StoreError("MongoDB is bound to a different Alpaca account")

        await self.db.account.update_one({"id": "account"}, {"$set": {
            "broker_env": "paper",
            "equity": float(account.get("equity") or bound.get("equity") or 0),
            "cash": float(account.get("cash") or bound.get("cash") or 0),
            "buying_power": float(account.get("options_buying_power") or account.get("buying_power")
                                  or bound.get("buying_power") or 0),
            "day_start_equity": float(account.get("last_equity") or bound.get("day_start_equity")
                                      or account.get("equity") or 0),
            "updated_at": utcnow().isoformat(),
        }})

    async def close(self):
        self.client.close()


async def connect_store(mongo_url: str, db_name: str) -> WorkerStore:
    client = AsyncIOMotorClient(
        mongo_url,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
        socketTimeoutMS=15000,
        appname="petra-paper-worker",
    )
    try:
        await client.admin.command("ping")
    except Exception as exc:
        client.close()
        raise StoreError("Persistent MongoDB is unavailable") from exc

    store = WorkerStore(client, client[db_name])
    await store.ensure_indexes()
    return store


class Lease:
    def __init__(self, store: WorkerStore, owner: str, ttl_seconds: int):
        self.store = store
        self.owner = owner
        self.ttl_seconds = ttl_seconds
        self.acquired = False

    async def __aenter__(self):
        now = utcnow()
        expires = now + timedelta(seconds=self.ttl_seconds)
        try:
            doc = await self.store.db.locks.find_one_and_update(
                {
                    "_id": "trading",
                    "$or": [
                        {"expires_at": {"$lte": now}},
                        {"owner": self.owner},
                        {"expires_at": {"$exists": False}},
                    ],
                },
                {"$set": {"owner": self.owner, "expires_at": expires, "updated_at": now}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            raise LeaseNotAcquired("Another Petra worker owns the trading lease") from exc

        if not doc or doc.get("owner") != self.owner:
            raise LeaseNotAcquired("Another Petra worker owns the trading lease")
        self.acquired = True
        return self

    async def assert_owned(self):
        doc = await self.store.db.locks.find_one({"_id": "trading"})
        if not doc or doc.get("owner") != self.owner or doc.get("expires_at") <= utcnow():
            raise LeaseNotAcquired("Trading lease is no longer owned by this worker")

    async def __aexit__(self, exc_type, exc, tb):
        if self.acquired:
            await self.store.db.locks.delete_one({"_id": "trading", "owner": self.owner})
        self.acquired = False

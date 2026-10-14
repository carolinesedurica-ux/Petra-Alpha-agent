"""Offline tests: no credentials, network, scheduler, or broker orders."""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
from mongomock_motor import AsyncMongoMockClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loss_protection import check_losses, protected_entry, EntryBlocked
from models import RiskConfig

NOW = datetime(2026, 10, 14, 15, tzinfo=timezone.utc)
CFG = RiskConfig().model_dump()


def account(equity=100000, baseline=100000):
    return {"equity": equity, "day_start_equity": baseline, "initial_equity": 100000}


@pytest.mark.asyncio
async def test_daily_boundary_restart_recovery_and_new_day():
    db = AsyncMongoMockClient().test
    await check_losses(db, account(98000.01), CFG, NOW)
    with pytest.raises(EntryBlocked, match="Daily"):
        await check_losses(db, account(98000), CFG, NOW)
    # New invocation sharing only the persistent database, after equity recovers.
    with pytest.raises(EntryBlocked, match="Daily"):
        await check_losses(db, account(101000), CFG, NOW)
    await check_losses(db, account(101000, 101000), CFG, NOW + timedelta(days=1))


@pytest.mark.asyncio
async def test_drawdown_peak_boundary_restart_and_day_rollover():
    db = AsyncMongoMockClient().test
    await check_losses(db, account(110000), CFG, NOW)
    await check_losses(db, account(104500.01, 104500.01), CFG, NOW + timedelta(days=1))
    with pytest.raises(EntryBlocked, match="drawdown"):
        await check_losses(db, account(104500, 104500), CFG, NOW + timedelta(days=1))
    with pytest.raises(EntryBlocked, match="drawdown"):
        await check_losses(db, account(120000, 120000), CFG, NOW + timedelta(days=2))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["equity", "day_start_equity", "initial_equity"])
@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), "bad", True])
async def test_missing_invalid_data(field, value):
    data = account()
    data[field] = value
    with pytest.raises(EntryBlocked):
        await check_losses(AsyncMongoMockClient().test, data, CFG, NOW)


@pytest.mark.asyncio
async def test_stale_data_and_configurable_limits():
    db = AsyncMongoMockClient().test
    data = {**account(), "mode": "live", "updated_at": (NOW - timedelta(seconds=61)).isoformat()}
    with pytest.raises(EntryBlocked, match="Stale"):
        await check_losses(db, data, CFG, NOW)
    with pytest.raises(EntryBlocked, match="Daily"):
        await check_losses(db, account(99000), {**CFG, "max_daily_loss_pct": 1}, NOW)


@pytest.mark.asyncio
async def test_history_peak_migration_and_corrupt_state():
    db = AsyncMongoMockClient().test
    await db.pnl_snapshots.insert_one({"equity": 110000})
    with pytest.raises(EntryBlocked, match="drawdown"):
        await check_losses(db, account(), CFG, NOW)
    await db.risk_state.update_one({"_id": "equity"}, {"$set": {"peak": None}})
    with pytest.raises(EntryBlocked):
        await check_losses(db, account(), CFG, NOW)


PROPOSAL = {"underlying": "SPY", "max_risk": 100, "credit_width_ratio": .3,
            "worst_bid_ask": .05, "min_oi": 500}


class Broker:
    mode = "live"
    is_paper = True
    trading = "paper"

    def __init__(self, db):
        self.db = db
        self.calls = 0
        self.positions = []
        self.orders = []
        self.equity = 100000
        self.raise_submit = False
        self.result = {"status": "filled", "order_id": "order-1"}

    async def _req(self, method, base, path, **kwargs):
        return self.positions if path == "/positions" else self.orders

    async def recompute_equity(self, positions):
        return self.equity, self.equity

    async def get_account(self):
        return {**account(self.equity), "mode": "live", "updated_at": datetime.now(timezone.utc).isoformat()}

    @protected_entry
    async def place_mleg(self, proposal, cycle_id=""):
        self.calls += 1
        await asyncio.sleep(0)
        if self.raise_submit:
            raise RuntimeError("timeout after submission")
        return self.result


@pytest.mark.asyncio
async def test_concurrent_entries_and_restart_before_position_persistence():
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    results = await asyncio.gather(broker.place_mleg(PROPOSAL), broker.place_mleg(PROPOSAL), return_exceptions=True)
    assert broker.calls == 1
    assert sum(isinstance(r, EntryBlocked) for r in results) == 1
    restarted = Broker(db)
    with pytest.raises(EntryBlocked):
        await restarted.place_mleg(PROPOSAL)
    assert restarted.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["db", "positions", "orders", "missing", "funded", "loss"])
async def test_entry_fail_closed(source):
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    if source == "db":
        await db.positions.insert_one({"status": "open", "underlying": "QQQ"})
    elif source == "positions":
        broker.positions = [{"symbol": "orphan"}]
    elif source == "orders":
        broker.orders = [{"id": "pending"}]
    elif source == "missing":
        broker.positions = None
    elif source == "funded":
        broker.is_paper = False
    else:
        broker.equity = 98000
    with pytest.raises(EntryBlocked):
        await broker.place_mleg(PROPOSAL)
    assert broker.calls == 0


@pytest.mark.asyncio
async def test_uncertain_submission_never_retries_after_restart():
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    broker.raise_submit = True
    with pytest.raises(RuntimeError):
        await broker.place_mleg(PROPOSAL)
    with pytest.raises(EntryBlocked):
        await Broker(db).place_mleg(PROPOSAL)


@pytest.mark.asyncio
async def test_closed_position_release_requires_flat_broker():
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    await broker.place_mleg(PROPOSAL)
    await db.positions.insert_one({"alpaca_order_id": "order-1", "status": "closed"})
    broker.positions = [{"symbol": "assignment"}]
    with pytest.raises(EntryBlocked):
        await broker.place_mleg(PROPOSAL)
    broker.positions = []
    await broker.place_mleg(PROPOSAL)
    assert broker.calls == 2


@pytest.mark.asyncio
async def test_confirmed_zero_fill_can_retry_but_partial_cannot():
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    broker.result = {"status": "unfilled", "alpaca_status": "canceled", "filled_qty": 0, "order_id": "cancel"}
    await broker.place_mleg(PROPOSAL)
    await broker.place_mleg(PROPOSAL)
    broker.result = {"status": "partial_review", "filled_qty": 1, "order_id": "partial"}
    await broker.place_mleg(PROPOSAL)
    with pytest.raises(EntryBlocked):
        await Broker(db).place_mleg(PROPOSAL)


def test_invalid_config_and_one_position_gate():
    from pydantic import ValidationError
    from engines import risk_gate
    for value in [0, -1, float("nan"), float("inf"), 101]:
        with pytest.raises(ValidationError):
            RiskConfig(max_drawdown_pct=value)
    checks, passed, _ = risk_gate(PROPOSAL, CFG, 100000,
        [{"underlying": "QQQ", "status": "open", "max_risk": 100}])
    assert not passed
    assert not checks[1]["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["equity", "day:2026-10-14"])
async def test_deleted_persistent_state_blocks_reinitialization(key):
    db = AsyncMongoMockClient().test
    await check_losses(db, account(), CFG, NOW)
    await db.risk_state.delete_one({"_id": key})
    with pytest.raises(EntryBlocked, match="state missing"):
        await check_losses(db, account(), CFG, NOW)


@pytest.mark.asyncio
async def test_cycle_manages_exits_during_lockout(monkeypatch):
    import agent
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    broker.mode = "mock"
    broker.equity = 98000
    managed = []
    async def advance():
        return {}
    async def manage(*args):
        managed.append(True)
        return [{"reason": "stop_loss"}]
    async def snapshot(*args):
        return None
    broker.advance_market = advance
    monkeypatch.setattr(agent, "manage_positions", manage)
    monkeypatch.setattr(agent, "record_snapshot", snapshot)
    result = await agent.run_cycle(db, broker, force=True)
    assert managed == [True]
    assert result["status"] == "risk_lockout"
    assert broker.calls == 0


def test_both_execution_classes_use_guard():
    from alpaca import LiveAlpaca, MockAlpaca
    assert hasattr(LiveAlpaca.place_mleg, "__wrapped__")
    assert hasattr(MockAlpaca.place_mleg, "__wrapped__")


@pytest.mark.asyncio
async def test_manual_mock_entry_cannot_simulate_protection_rejection():
    # Execute the real handler without importing server startup/database/scheduler.
    import ast
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    handler = next(n for n in ast.parse(source).body
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "manual_open_position")
    handler.decorator_list = []
    handler.args.defaults = []
    class HTTPError(Exception):
        def __init__(self, status_code, detail):
            self.status_code = status_code
            self.detail = detail
    db = AsyncMongoMockClient().test
    broker = Broker(db)
    broker.mode = "mock"
    broker.equity = 98000
    async def config(db):
        return CFG
    from engines import risk_gate
    env = {"db": db, "alpaca": broker, "get_config": config, "risk_gate": risk_gate,
           "new_id": lambda: "manual-test", "EntryBlocked": EntryBlocked, "HTTPException": HTTPError}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "server.py", "exec"), env)
    with pytest.raises(HTTPError) as error:
        await env["manual_open_position"]({"proposal": PROPOSAL, "paper_sim": True})
    assert error.value.status_code == 400
    assert "Entry protection" in error.value.detail
    assert broker.calls == 0
    assert await db.positions.count_documents({}) == 0


@pytest.mark.asyncio
async def test_storage_failure_prevents_submission():
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    db = AsyncMongoMockClient().test
    broken = SimpleNamespace(find_one=AsyncMock(return_value=None),
        update_one=AsyncMock(side_effect=RuntimeError("storage unavailable")))
    broker = Broker(SimpleNamespace(risk_state=broken, account=db.account,
        pnl_snapshots=db.pnl_snapshots, config=db.config, positions=db.positions,
        entry_reservations=db.entry_reservations))
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await broker.place_mleg(PROPOSAL)
    assert broker.calls == 0

"""Adversarial, OFFLINE safety tests. No Alpaca credentials or live network calls."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from live_pilot import (
    LivePilotBroker, _snapshot_metrics, manage_open_position, maybe_enter,
)
from live_pilot_settings import LivePilotSettingsError
from store import StoreError


def run(coro):
    return asyncio.run(coro)


def broker_settings(**overrides):
    values = {
        "can_submit": True,
        "symbols": ("SPY", "QQQ"),
        "trade_notional_usd": 5.0,
        "max_allocation_pct": 20.0,
        "entry_momentum_pct": 0.15,
        "dry_run": True,
        "run_id": "mock-ci-test-1",
        "stop_loss_pct": .60,
        "take_profit_pct": .80,
        "max_hold_minutes": 120,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def snapshot(age_seconds=10):
    t = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {
        "latestTrade": {"p": 600, "t": t},
        "dailyBar": {"o": 597},
        "prevDailyBar": {"c": 595},
    }


def entry_fakes(positions=None, orders=None):
    broker = SimpleNamespace(
        positions=AsyncMock(return_value=positions or []),
        open_orders=AsyncMock(return_value=orders or []),
        asset=AsyncMock(return_value={"tradable": True, "fractionable": True}),
        submit=AsyncMock(side_effect=AssertionError("LIVE POST MUST NOT BE CALLED")),
    )
    db = SimpleNamespace(
        micro_positions=SimpleNamespace(
            find_one=AsyncMock(return_value=None),
            count_documents=AsyncMock(return_value=0),
        ),
        micro_decisions=SimpleNamespace(insert_one=AsyncMock()),
    )
    return db, broker


@pytest.mark.parametrize("foreign", [
    {"positions": [{"symbol": "VTI", "qty": "1", "side": "long"}]},
    {"orders": [{"symbol": "VTI", "status": "new"}]},
])
def test_foreign_broker_activity_blocks_new_entry(foreign):
    db, broker = entry_fakes(**foreign)
    result = run(maybe_enter(
        db, broker, broker_settings(), {"SPY": snapshot()},
        {"cash": "20"}, {},
    ))
    assert result == "foreign_account_activity"
    broker.submit.assert_not_awaited()
    db.micro_decisions.insert_one.assert_not_awaited()


def test_20_dollar_stage_caps_shown_shadow_order_at_four_dollars():
    db, broker = entry_fakes()
    result = run(maybe_enter(
        db, broker, broker_settings(), {"SPY": snapshot(), "QQQ": snapshot()},
        {"cash": "20"}, {},
    ))
    assert result == "shadow_entry"
    record = db.micro_decisions.insert_one.await_args.args[0]
    assert record["notional"] == 4.0  # min($5 configured, 20% of available $20)
    broker.submit.assert_not_awaited()


def test_market_snapshot_requires_fresh_timestamp():
    assert _snapshot_metrics("SPY", snapshot(10)) is not None
    assert _snapshot_metrics("SPY", snapshot(240)) is None
    assert _snapshot_metrics("SPY", {
        "dailyBar": {"c": 600, "o": 597},
        "prevDailyBar": {"c": 595},
    }) is None
    assert _snapshot_metrics("SPY", snapshot(-60)) is None


def test_extra_broker_quantity_is_not_sold_as_if_all_petra_owned():
    db = SimpleNamespace(micro_positions=SimpleNamespace(update_one=AsyncMock()))
    broker = SimpleNamespace(
        positions=AsyncMock(return_value=[
            {"symbol": "SPY", "qty": "0.250000000", "side": "long"},
        ]),
        submit=AsyncMock(side_effect=AssertionError("UNAUTHORIZED SELL")),
    )
    position = {"id": "p1", "symbol": "SPY", "qty": 0.2}
    with pytest.raises(StoreError, match="quantity mismatch"):
        run(manage_open_position(
            db, broker, broker_settings(), position,
            {"price": 605}, {},
        ))
    broker.submit.assert_not_awaited()


def order_broker(*, can_submit=True):
    orders = SimpleNamespace(insert_one=AsyncMock(), update_one=AsyncMock())
    db = SimpleNamespace(micro_orders=orders)
    return LivePilotBroker(broker_settings(can_submit=can_submit), db), orders


def buy_payload(client_id="petra-micro-entry-offline-test"):
    return {
        "symbol": "SPY", "notional": "4.00", "side": "buy",
        "type": "market", "time_in_force": "day",
        "client_order_id": client_id,
    }


def test_disarmed_path_cannot_reach_order_endpoint():
    broker, journal = order_broker(can_submit=False)
    broker.req = AsyncMock(side_effect=AssertionError("NO BROKER IO"))
    with pytest.raises(LivePilotSettingsError, match="not armed"):
        run(broker.submit(buy_payload(), "entry"))
    journal.insert_one.assert_not_awaited()
    broker.req.assert_not_awaited()


def test_journal_is_persisted_before_funded_post_and_stays_unsettled_until_position():
    broker, journal = order_broker()
    events = []

    async def record(doc):
        events.append("db_intent")
        assert doc["settled"] is False
        assert doc["client_order_id"] == buy_payload()["client_order_id"]

    async def post(method, base, path, **kwargs):
        assert method == "POST"
        assert events == ["db_intent"]
        events.append("broker_post")
        return {"id": "broker-1"}

    broker.db.micro_orders.insert_one = record
    broker.req = post
    broker.await_order = AsyncMock(return_value={
        "id": "broker-1", "status": "filled", "filled_qty": "0.006666667",
        "qty": "0.006666667", "filled_avg_price": "600",
        "client_order_id": buy_payload()["client_order_id"],
    })
    result = run(broker.submit(buy_payload(), "entry"))
    assert events == ["db_intent", "broker_post"]
    assert result["status"] == "filled"
    assert journal.update_one.await_args.args[1]["$set"]["settled"] is False


def test_post_timeout_looks_up_client_id_and_blocks_resubmission():
    broker, journal = order_broker()
    calls = []

    async def uncertain(method, base, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            raise TimeoutError("Broker accepted but network response lost")
        assert method == "GET" and path == "/orders:by_client_order_id"
        return {"id": "already-at-broker", "status": "filled"}

    broker.req = uncertain
    with pytest.raises(StoreError, match="uncertain"):
        run(broker.submit(buy_payload(), "entry"))
    assert calls == [("POST", "/orders"), ("GET", "/orders:by_client_order_id")]
    payload = journal.update_one.await_args.args[1]["$set"]
    assert payload["status"] == "submission_unknown"
    assert payload["broker_order_id"] == "already-at-broker"


def test_cancelled_partial_fill_never_settles_order_without_position_reconciliation():
    broker, journal = order_broker()
    broker.req = AsyncMock(return_value={"id": "broker-1"})
    broker.await_order = AsyncMock(return_value={
        "id": "broker-1", "status": "canceled", "filled_qty": "0.001",
        "qty": "0.006666667", "filled_avg_price": "600",
        "client_order_id": buy_payload()["client_order_id"],
    })
    result = run(broker.submit(buy_payload(), "entry"))
    assert result["status"] == "partial_review"
    assert journal.update_one.await_args.args[1]["$set"]["settled"] is False


def test_duplicate_client_order_id_is_stopped_at_db_before_post():
    broker, journal = order_broker()
    journal.insert_one.side_effect = RuntimeError("Unique client_order_id index collision")
    broker.req = AsyncMock(side_effect=AssertionError("NO DUPLICATE POST"))
    with pytest.raises(RuntimeError, match="collision"):
        run(broker.submit(buy_payload(), "entry"))
    broker.req.assert_not_awaited()

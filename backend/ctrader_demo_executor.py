"""Guarded autonomous cTrader demo executor for Petra.

Runs one decision/execution cycle against the explicitly whitelisted cTrader DEMO
account. It consumes Petra's real-market US500 analysis, then either:
- opens one minimum-volume demo position when all signal/risk gates pass,
- closes Petra's own demo position on a strong opposite signal, or
- takes no action.

This module can never use the live cTrader endpoint. It requires dedicated demo
execution flags in addition to Petra's existing fail-closed settings.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAClosePositionReq,
    ProtoOAExecutionEvent,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOANewOrderReq,
    ProtoOAOrderErrorEvent,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType,
    ProtoOAOrderType,
    ProtoOATradeSide,
)
from twisted.internet import reactor

from ctrader_settings import CTraderSettingsError, load_ctrader_settings

log = logging.getLogger("petra.ctrader.demo_executor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
ROOT = Path(__file__).resolve().parent
LABEL = "PETRA_DEMO"
DAILY_LOSS_LIMIT = max(0.25, float(os.environ.get("PETRA_CTRADER_DEMO_DAILY_LOSS_USD") or "1.00"))
STATE_FILE = Path(os.environ.get("PETRA_CTRADER_DEMO_STATE_FILE") or "/tmp/petra_ctrader_demo_state.json")


def _bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _demo_gate(settings) -> None:
    if settings.environment != "demo":
        raise RuntimeError("Demo executor refuses any non-demo cTrader endpoint")
    if not settings.dry_run:
        raise RuntimeError("PETRA_CTRADER_DRY_RUN must remain true for demo executor")
    if settings.armed:
        raise RuntimeError("PETRA_CTRADER_PILOT_ARMED must remain false for demo executor")
    if _bool("ALLOW_LIVE_TRADING", False):
        raise RuntimeError("ALLOW_LIVE_TRADING must remain false")
    if not _bool("PETRA_CTRADER_DEMO_EXECUTION_ENABLED", False):
        raise RuntimeError("PETRA_CTRADER_DEMO_EXECUTION_ENABLED is not enabled")
    if (os.environ.get("PETRA_CTRADER_DEMO_EXECUTION_CONFIRM") or "").strip() != "CTRADER_DEMO_AUTONOMOUS":
        raise RuntimeError("Demo execution confirmation is missing")


def _run_analysis() -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "ctrader_autonomous_bridge.py")],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
        check=False,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Autonomous analysis returned no data")
    payload = json.loads(lines[-1])
    if completed.returncode != 0 or payload.get("status") != "ok":
        raise RuntimeError(payload.get("error") or "Autonomous analysis failed")
    return payload


def _load_day_state(balance: float) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    data: dict[str, Any] = {}
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
    except Exception:
        data = {}
    if data.get("date") != today:
        data = {"date": today, "day_start_balance": balance, "cycles": 0, "orders_opened": 0}
        try:
            STATE_FILE.write_text(json.dumps(data))
        except Exception:
            pass
    return data


def _save_day_state(data: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


class ExecState:
    def __init__(self, analysis: dict[str, Any], settings) -> None:
        self.analysis = analysis
        self.settings = settings
        self.failed = False
        self.positions: list[Any] = []
        self.pending_orders: list[Any] = []
        self.action = "NO_ACTION"
        self.summary: dict[str, Any] = {
            "broker": "ctrader",
            "mode": "autonomous_demo_execution",
            "status": "pending",
            "account_id": settings.expected_account_id,
            "symbol": analysis.get("broker_symbol") or analysis.get("symbol") or "US500",
            "live_trading": False,
            "demo_execution": True,
            "decision": analysis.get("analysis", {}).get("decision", "NO_TRADE"),
            "final_decision": analysis.get("final_decision", "NO_TRADE"),
        }


settings = None
client = None
state: ExecState | None = None


def _fail(message: str) -> None:
    if state is None or state.failed:
        return
    state.failed = True
    state.summary.update({"status": "error", "error": message})
    log.error(message)
    if reactor.running:
        reactor.callLater(0, reactor.stop)


def _finish(action: str, **extra) -> None:
    if state is None:
        return
    state.action = action
    state.summary.update({"status": "ok", "action": action, **extra})
    if reactor.running:
        reactor.callLater(0, reactor.stop)


def _on_error(failure) -> None:
    text = getattr(failure, "getErrorMessage", lambda: str(failure))()
    _fail(f"cTrader request failed: {text}")


def _send(request) -> None:
    d = client.send(request, responseTimeoutInSeconds=20)
    d.addErrback(_on_error)


def _account_list() -> None:
    req = ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = settings.access_token
    _send(req)


def _account_auth() -> None:
    req = ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.accessToken = settings.access_token
    _send(req)


def _reconcile() -> None:
    req = ProtoOAReconcileReq()
    req.ctidTraderAccountId = settings.expected_account_id
    _send(req)


def _position_label(position: Any) -> str:
    return str(getattr(getattr(position, "tradeData", None), "label", "") or "")


def _position_symbol(position: Any) -> int:
    return int(getattr(getattr(position, "tradeData", None), "symbolId", 0) or 0)


def _position_side(position: Any) -> int:
    return int(getattr(getattr(position, "tradeData", None), "tradeSide", 0) or 0)


def _position_volume(position: Any) -> int:
    return int(getattr(getattr(position, "tradeData", None), "volume", 0) or 0)


def _open_demo_order(direction: str) -> None:
    analysis = state.analysis
    signal = analysis.get("analysis") or {}
    symbol_id = int(analysis.get("symbol_id") or 0)
    units = float(analysis.get("minimum_volume_units") or 0.0)
    volume = int(round(units * 100.0))
    price = float(signal.get("price") or 0.0)
    stop = float(signal.get("suggested_stop_loss") or 0.0)
    target = float(signal.get("suggested_take_profit") or 0.0)
    if symbol_id <= 0 or volume <= 0 or price <= 0 or stop <= 0 or target <= 0:
        _fail("Analysis did not provide a complete executable demo setup")
        return
    stop_distance = abs(price - stop)
    target_distance = abs(target - price)
    relative_stop = int(round(stop_distance * 100000.0))
    relative_target = int(round(target_distance * 100000.0))
    if relative_stop <= 0 or relative_target <= 0:
        _fail("Invalid relative protection distances")
        return

    req = ProtoOANewOrderReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.symbolId = symbol_id
    req.orderType = ProtoOAOrderType.Value("MARKET")
    req.tradeSide = ProtoOATradeSide.Value(direction)
    req.volume = volume
    req.relativeStopLoss = relative_stop
    req.relativeTakeProfit = relative_target
    req.label = LABEL
    req.comment = "Petra autonomous DEMO pilot"
    req.clientOrderId = f"petra-demo-{int(time.time())}"
    state.action = f"OPEN_{direction}"
    state.summary.update({
        "requested_volume_cents": volume,
        "requested_volume_units": units,
        "reference_price": price,
        "relative_stop_distance": round(stop_distance, 5),
        "relative_target_distance": round(target_distance, 5),
    })
    _send(req)


def _close_demo_position(position: Any, reason: str) -> None:
    position_id = int(getattr(position, "positionId", 0) or 0)
    volume = _position_volume(position)
    if position_id <= 0 or volume <= 0:
        _fail("Petra demo position has invalid position id or volume")
        return
    req = ProtoOAClosePositionReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.positionId = position_id
    req.volume = volume
    state.action = "CLOSE_POSITION"
    state.summary.update({"close_position_id": position_id, "close_reason": reason})
    _send(req)


def _decide_after_reconcile() -> None:
    analysis = state.analysis
    signal = analysis.get("analysis") or {}
    symbol_id = int(analysis.get("symbol_id") or 0)
    petra_positions = [p for p in state.positions if _position_label(p) == LABEL and _position_symbol(p) == symbol_id]
    foreign_positions = [p for p in state.positions if p not in petra_positions]

    if foreign_positions:
        _finish("NO_ACTION", reason="Non-Petra position is open; autonomous demo executor will not interfere")
        return
    if state.pending_orders:
        _finish("NO_ACTION", reason="Pending broker order exists; no new autonomous action")
        return
    if len(petra_positions) > 1:
        _fail("More than one Petra demo position detected; fail-closed")
        return

    if petra_positions:
        position = petra_positions[0]
        current_side = "BUY" if _position_side(position) == ProtoOATradeSide.Value("BUY") else "SELL"
        decision = str(signal.get("decision") or "NO_TRADE")
        confidence = float(signal.get("confidence") or 0.0)
        floor = float(signal.get("confidence_floor") or 1.0)
        opposite = (current_side == "BUY" and decision == "SELL") or (current_side == "SELL" and decision == "BUY")
        if opposite and confidence >= floor:
            _close_demo_position(position, f"strong opposite {decision} signal at confidence {confidence:.3f}")
            return
        _finish("HOLD", reason="Existing Petra demo position retained; broker SL/TP remains active", current_side=current_side)
        return

    balance = float(analysis.get("balance_usd") or 0.0)
    day = _load_day_state(balance)
    daily_loss = max(0.0, float(day.get("day_start_balance", balance)) - balance)
    state.summary["daily_loss_usd"] = round(daily_loss, 2)
    state.summary["daily_loss_limit_usd"] = DAILY_LOSS_LIMIT
    if daily_loss >= DAILY_LOSS_LIMIT:
        _finish("NO_ACTION", reason="Daily loss stop reached; no new risk")
        return
    if not bool(analysis.get("shadow_actionable")):
        _finish("NO_ACTION", reason=signal.get("reason") or "No actionable market setup")
        return
    direction = str(analysis.get("final_decision") or "NO_TRADE")
    if direction not in {"BUY", "SELL"}:
        _finish("NO_ACTION", reason="Final decision is NO_TRADE")
        return
    _open_demo_order(direction)


def _on_message(_client, message) -> None:
    try:
        pt = message.payloadType
        if pt == ProtoOAApplicationAuthRes().payloadType:
            _account_list(); return
        if pt == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            res = Protobuf.extract(message)
            ids = [int(a.ctidTraderAccountId) for a in res.ctidTraderAccount]
            if settings.expected_account_id not in ids:
                _fail("Expected cTrader demo account is not authorized by token"); return
            _account_auth(); return
        if pt == ProtoOAAccountAuthRes().payloadType:
            res = Protobuf.extract(message)
            if int(res.ctidTraderAccountId) != settings.expected_account_id:
                _fail("Authenticated cTrader account does not match whitelist"); return
            _reconcile(); return
        if pt == ProtoOAReconcileRes().payloadType:
            res = Protobuf.extract(message)
            state.positions = list(res.position)
            state.pending_orders = list(res.order)
            _decide_after_reconcile(); return
        if pt == ProtoOAOrderErrorEvent().payloadType:
            res = Protobuf.extract(message)
            _fail(f"Order error {getattr(res, 'errorCode', '')}: {getattr(res, 'description', '')}")
            return
        if pt == ProtoOAExecutionEvent().payloadType:
            res = Protobuf.extract(message)
            et = int(getattr(res, "executionType", 0) or 0)
            if et == ProtoOAExecutionType.Value("ORDER_REJECTED"):
                _fail("cTrader rejected the demo order")
                return
            if et in {ProtoOAExecutionType.Value("ORDER_FILLED"), ProtoOAExecutionType.Value("ORDER_PARTIAL_FILL")}:
                order = getattr(res, "order", None)
                position = getattr(res, "position", None)
                order_id = int(getattr(order, "orderId", 0) or 0) if order is not None else None
                position_id = int(getattr(position, "positionId", 0) or 0) if position is not None else None
                execution_price = float(getattr(order, "executionPrice", 0.0) or 0.0) if order is not None else None
                if state.action.startswith("OPEN_"):
                    day = _load_day_state(float(state.analysis.get("balance_usd") or 0.0))
                    day["orders_opened"] = int(day.get("orders_opened", 0)) + 1
                    day["cycles"] = int(day.get("cycles", 0)) + 1
                    _save_day_state(day)
                _finish(
                    state.action,
                    order_id=order_id,
                    position_id=position_id,
                    execution_price=execution_price,
                    execution_type=("PARTIAL_FILL" if et == ProtoOAExecutionType.Value("ORDER_PARTIAL_FILL") else "FILLED"),
                )
                return
    except Exception as exc:
        _fail(f"Demo executor parser failed: {type(exc).__name__}: {exc}")


def _connected(_client) -> None:
    req = ProtoOAApplicationAuthReq()
    req.clientId = settings.client_id
    req.clientSecret = settings.client_secret
    _send(req)


def _disconnected(_client, reason) -> None:
    if state is not None and not state.failed and state.summary.get("status") == "pending":
        text = getattr(reason, "getErrorMessage", lambda: str(reason))()
        _fail(f"cTrader disconnected before demo cycle completed: {text}")


def main() -> int:
    global settings, client, state
    try:
        settings = load_ctrader_settings()
        _demo_gate(settings)
        analysis = _run_analysis()
    except (CTraderSettingsError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({
            "broker": "ctrader",
            "mode": "autonomous_demo_execution",
            "status": "blocked",
            "live_trading": False,
            "demo_execution": False,
            "error": str(exc),
        }, sort_keys=True))
        return 2

    state = ExecState(analysis, settings)
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(_connected)
    client.setDisconnectedCallback(_disconnected)
    client.setMessageReceivedCallback(_on_message)
    client.startService()
    reactor.callLater(45, lambda: _fail("cTrader demo execution cycle timed out"))
    reactor.run()

    print(json.dumps(state.summary, sort_keys=True))
    return 1 if state.failed else 0


if __name__ == "__main__":
    sys.exit(main())

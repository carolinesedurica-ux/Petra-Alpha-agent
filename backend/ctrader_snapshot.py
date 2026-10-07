"""Read-only cTrader account snapshot for Petra.

Authenticates the configured cTrader application and whitelisted demo account,
then returns account balance plus counts of open positions and pending orders.
No order submission messages are imported or sent.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
    ProtoOATraderReq,
    ProtoOATraderRes,
)
from twisted.internet import reactor

from ctrader_settings import CTraderSettingsError, load_ctrader_settings

log = logging.getLogger("petra.ctrader.snapshot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class SnapshotState:
    def __init__(self) -> None:
        self.trader_received = False
        self.reconcile_received = False
        self.failed = False
        self.summary: dict[str, Any] = {
            "service": "Petra Alpha Agent",
            "broker": "ctrader",
            "mode": "read_only",
            "execution_enabled": False,
        }


state = SnapshotState()
settings = None
client = None


def _fail(message: str) -> None:
    if state.failed:
        return
    state.failed = True
    state.summary["status"] = "error"
    state.summary["error"] = message
    log.error(message)
    if reactor.running:
        reactor.callLater(0, reactor.stop)


def _on_error(failure) -> None:
    text = getattr(failure, "getErrorMessage", lambda: str(failure))()
    _fail(f"cTrader request failed: {text}")


def _send(request) -> None:
    deferred = client.send(request, responseTimeoutInSeconds=20)
    deferred.addErrback(_on_error)


def _send_account_list() -> None:
    request = ProtoOAGetAccountListByAccessTokenReq()
    request.accessToken = settings.access_token
    _send(request)


def _send_account_auth() -> None:
    request = ProtoOAAccountAuthReq()
    request.ctidTraderAccountId = settings.expected_account_id
    request.accessToken = settings.access_token
    _send(request)


def _send_snapshot_reads() -> None:
    trader = ProtoOATraderReq()
    trader.ctidTraderAccountId = settings.expected_account_id
    _send(trader)

    reconcile = ProtoOAReconcileReq()
    reconcile.ctidTraderAccountId = settings.expected_account_id
    _send(reconcile)


def _maybe_done() -> None:
    if state.trader_received and state.reconcile_received and not state.failed:
        state.summary["status"] = "ok"
        state.summary["authenticated"] = True
        reactor.callLater(0, reactor.stop)


def _on_message(_client, message) -> None:
    try:
        payload_type = message.payloadType

        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            _send_account_list()
            return

        if payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            response = Protobuf.extract(message)
            account_ids = [int(a.ctidTraderAccountId) for a in response.ctidTraderAccount]
            if settings.expected_account_id not in account_ids:
                _fail("Expected cTrader account is not authorized by this access token")
                return
            state.summary["authorized_account_count"] = len(account_ids)
            state.summary["account_id"] = settings.expected_account_id
            _send_account_auth()
            return

        if payload_type == ProtoOAAccountAuthRes().payloadType:
            response = Protobuf.extract(message)
            connected_id = int(response.ctidTraderAccountId)
            if connected_id != settings.expected_account_id:
                _fail("cTrader authenticated a different account than Petra's whitelist")
                return
            _send_snapshot_reads()
            return

        if payload_type == ProtoOATraderRes().payloadType:
            response = Protobuf.extract(message)
            trader = response.trader
            money_digits = int(getattr(trader, "moneyDigits", 2) or 2)
            divisor = 10 ** money_digits
            state.summary["balance"] = round(float(trader.balance) / divisor, money_digits)
            state.summary["money_digits"] = money_digits
            state.summary["broker_name"] = str(getattr(trader, "brokerName", "") or "")
            state.summary["environment"] = settings.environment
            state.trader_received = True
            _maybe_done()
            return

        if payload_type == ProtoOAReconcileRes().payloadType:
            response = Protobuf.extract(message)
            state.summary["open_positions"] = len(response.position)
            state.summary["pending_orders"] = len(response.order)
            state.reconcile_received = True
            _maybe_done()
            return

    except Exception as exc:
        _fail(f"cTrader snapshot parser failed: {type(exc).__name__}: {exc}")


def _connected(_client) -> None:
    request = ProtoOAApplicationAuthReq()
    request.clientId = settings.client_id
    request.clientSecret = settings.client_secret
    _send(request)


def _disconnected(_client, reason) -> None:
    if not state.failed and not (state.trader_received and state.reconcile_received):
        text = getattr(reason, "getErrorMessage", lambda: str(reason))()
        _fail(f"cTrader disconnected before snapshot completed: {text}")


def main() -> int:
    global settings, client
    try:
        settings = load_ctrader_settings()
    except CTraderSettingsError as exc:
        print(json.dumps({
            "service": "Petra Alpha Agent",
            "broker": "ctrader",
            "mode": "read_only",
            "status": "error",
            "execution_enabled": False,
            "error": str(exc),
        }))
        return 2

    if settings.environment != "demo":
        print(json.dumps({
            "service": "Petra Alpha Agent",
            "broker": "ctrader",
            "mode": "read_only",
            "status": "error",
            "execution_enabled": False,
            "error": "Petra cTrader snapshot is restricted to demo environment",
        }))
        return 3

    host = EndPoints.PROTOBUF_DEMO_HOST
    client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(_connected)
    client.setDisconnectedCallback(_disconnected)
    client.setMessageReceivedCallback(_on_message)
    client.startService()

    reactor.callLater(30, lambda: _fail("cTrader snapshot timed out"))
    reactor.run()

    print(json.dumps(state.summary, sort_keys=True))
    return 1 if state.failed else 0


if __name__ == "__main__":
    sys.exit(main())

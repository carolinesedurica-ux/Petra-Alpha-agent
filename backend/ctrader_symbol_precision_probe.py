"""Read-only cTrader symbol precision probe for Petra.

Authenticates the whitelisted demo account, fetches full metadata for US500,
and reports the broker's digits/pip precision. No order messages are imported.
"""
from __future__ import annotations

import json
import os
import re
import sys

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from twisted.internet import reactor

from ctrader_settings import CTraderSettingsError, load_ctrader_settings

TARGET = (os.environ.get("PETRA_CTRADER_AUTONOMOUS_SYMBOL") or "US500").strip()


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


class State:
    def __init__(self) -> None:
        self.failed = False
        self.symbol_id = None
        self.symbol_name = None
        self.summary = {
            "broker": "ctrader",
            "mode": "read_only_symbol_precision_probe",
            "orders_enabled": False,
            "execution_attempted": False,
        }


state = State()
settings = None
client = None


def _fail(message: str) -> None:
    if state.failed:
        return
    state.failed = True
    state.summary.update({"status": "error", "error": message})
    if reactor.running:
        reactor.callLater(0, reactor.stop)


def _send(req) -> None:
    d = client.send(req, responseTimeoutInSeconds=20)
    d.addErrback(lambda failure: _fail(getattr(failure, "getErrorMessage", lambda: str(failure))()))


def _on_message(_client, message) -> None:
    try:
        pt = message.payloadType
        if pt == ProtoOAApplicationAuthRes().payloadType:
            req = ProtoOAGetAccountListByAccessTokenReq(); req.accessToken = settings.access_token; _send(req); return
        if pt == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            res = Protobuf.extract(message)
            ids = [int(a.ctidTraderAccountId) for a in res.ctidTraderAccount]
            if settings.expected_account_id not in ids:
                _fail("Expected cTrader account is not authorized by token"); return
            req = ProtoOAAccountAuthReq(); req.ctidTraderAccountId = settings.expected_account_id; req.accessToken = settings.access_token; _send(req); return
        if pt == ProtoOAAccountAuthRes().payloadType:
            req = ProtoOASymbolsListReq(); req.ctidTraderAccountId = settings.expected_account_id; req.includeArchivedSymbols = False; _send(req); return
        if pt == ProtoOASymbolsListRes().payloadType:
            res = Protobuf.extract(message)
            target = _norm(TARGET)
            for symbol in res.symbol:
                name = str(getattr(symbol, "symbolName", "") or "")
                if bool(getattr(symbol, "enabled", True)) and _norm(name) == target:
                    state.symbol_id = int(symbol.symbolId)
                    state.symbol_name = name
                    break
            if state.symbol_id is None:
                _fail(f"{TARGET} unavailable on authorized account"); return
            req = ProtoOASymbolByIdReq(); req.ctidTraderAccountId = settings.expected_account_id; req.symbolId.append(state.symbol_id); _send(req); return
        if pt == ProtoOASymbolByIdRes().payloadType:
            res = Protobuf.extract(message)
            if not res.symbol:
                _fail("No full symbol metadata returned"); return
            symbol = res.symbol[0]
            digits = int(getattr(symbol, "digits", 0) or 0)
            pip_position = int(getattr(symbol, "pipPosition", 0) or 0)
            state.summary.update({
                "status": "ok",
                "account_id": settings.expected_account_id,
                "symbol": state.symbol_name,
                "symbol_id": state.symbol_id,
                "digits": digits,
                "pip_position": pip_position,
                "relative_precision_quantum": 10 ** max(0, 5 - digits),
                "min_volume_cents": int(getattr(symbol, "minVolume", 0) or 0),
                "step_volume_cents": int(getattr(symbol, "stepVolume", 0) or 0),
            })
            reactor.callLater(0, reactor.stop)
    except Exception as exc:
        _fail(f"precision probe failed: {type(exc).__name__}: {exc}")


def _connected(_client) -> None:
    req = ProtoOAApplicationAuthReq(); req.clientId = settings.client_id; req.clientSecret = settings.client_secret; _send(req)


def main() -> int:
    global settings, client
    try:
        settings = load_ctrader_settings()
    except CTraderSettingsError as exc:
        print(json.dumps({"status": "error", "mode": "read_only_symbol_precision_probe", "error": str(exc), "orders_enabled": False}))
        return 2
    if settings.environment != "demo" or not settings.dry_run or settings.armed or settings.can_submit:
        print(json.dumps({"status": "blocked", "mode": "read_only_symbol_precision_probe", "error": "Requires demo dry-run, unarmed, execution disabled", "orders_enabled": False}))
        return 2
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(_connected)
    client.setMessageReceivedCallback(_on_message)
    client.startService()
    reactor.callLater(35, lambda: _fail("symbol precision probe timed out"))
    reactor.run()
    print(json.dumps(state.summary, sort_keys=True))
    return 1 if state.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

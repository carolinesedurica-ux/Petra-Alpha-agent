"""Read-only cTrader symbol/margin probe for Petra Phase 2.

Authenticates one explicitly whitelisted account, discovers a small target list of
Pepperstone symbols, reads broker minimum/step volume metadata, and asks cTrader
for expected margin at the broker minimum volume. No order messages are imported
or sent by this script.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import deque
from typing import Any

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAExpectedMarginReq,
    ProtoOAExpectedMarginRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
    ProtoOATraderReq,
    ProtoOATraderRes,
)
from twisted.internet import reactor

from ctrader_pilot_risk import assess_candidate
from ctrader_settings import CTraderSettingsError, load_ctrader_settings

log = logging.getLogger("petra.ctrader.phase2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _targets() -> list[str]:
    raw = (os.environ.get("CTRADER_PROBE_SYMBOLS") or "EURUSD,XAUUSD,US500,NAS100").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


class ProbeState:
    def __init__(self) -> None:
        self.failed = False
        self.balance_usd = 0.0
        self.money_digits = 2
        self.open_positions = 0
        self.pending_orders = 0
        self.light_by_id: dict[int, dict[str, Any]] = {}
        self.full_by_id: dict[int, Any] = {}
        self.margin_queue: deque[int] = deque()
        self.margin_current: int | None = None
        self.results: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {
            "broker": "ctrader",
            "mode": "phase2_read_only_probe",
            "authenticated": False,
            "orders_enabled": False,
        }


state = ProbeState()
settings = None
client = None


def _fail(message: str) -> None:
    if state.failed:
        return
    state.failed = True
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
    req = ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = settings.access_token
    _send(req)


def _send_account_auth() -> None:
    req = ProtoOAAccountAuthReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.accessToken = settings.access_token
    _send(req)


def _send_trader() -> None:
    req = ProtoOATraderReq()
    req.ctidTraderAccountId = settings.expected_account_id
    _send(req)


def _send_reconcile() -> None:
    req = ProtoOAReconcileReq()
    req.ctidTraderAccountId = settings.expected_account_id
    _send(req)


def _send_symbol_list() -> None:
    req = ProtoOASymbolsListReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.includeArchivedSymbols = False
    _send(req)


def _send_symbol_details(ids: list[int]) -> None:
    req = ProtoOASymbolByIdReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.symbolId.extend(ids)
    _send(req)


def _send_next_margin() -> None:
    if not state.margin_queue:
        state.summary["authenticated"] = True
        state.summary["account_id"] = settings.expected_account_id
        state.summary["balance_usd"] = state.balance_usd
        state.summary["open_positions"] = state.open_positions
        state.summary["pending_orders"] = state.pending_orders
        state.summary["symbols"] = state.results
        state.summary["eligible_symbol_count"] = sum(1 for item in state.results if item.get("eligible"))
        reactor.callLater(0, reactor.stop)
        return

    symbol_id = state.margin_queue.popleft()
    symbol = state.full_by_id[symbol_id]
    min_volume = int(getattr(symbol, "minVolume", 0) or 0)
    if min_volume <= 0:
        _fail(f"Symbol {symbol_id} has no positive broker minimum volume")
        return
    state.margin_current = symbol_id
    req = ProtoOAExpectedMarginReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.symbolId = symbol_id
    req.volume.append(min_volume)
    _send(req)


def _on_message(_client, message) -> None:
    try:
        payload_type = message.payloadType

        if payload_type == ProtoOAApplicationAuthRes().payloadType:
            log.info("cTrader application authenticated")
            _send_account_list()
            return

        if payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            response = Protobuf.extract(message)
            ids = [int(a.ctidTraderAccountId) for a in response.ctidTraderAccount]
            if settings.expected_account_id not in ids:
                _fail("Expected cTrader account is not authorized by this token")
                return
            _send_account_auth()
            return

        if payload_type == ProtoOAAccountAuthRes().payloadType:
            response = Protobuf.extract(message)
            if int(response.ctidTraderAccountId) != settings.expected_account_id:
                _fail("Authenticated cTrader account does not match Petra whitelist")
                return
            log.info("Whitelisted cTrader account authenticated")
            _send_trader()
            return

        if payload_type == ProtoOATraderRes().payloadType:
            response = Protobuf.extract(message)
            trader = response.trader
            state.money_digits = int(getattr(trader, "moneyDigits", 2) or 2)
            state.balance_usd = round(float(trader.balance) / (10 ** state.money_digits), state.money_digits)
            _send_reconcile()
            return

        if payload_type == ProtoOAReconcileRes().payloadType:
            response = Protobuf.extract(message)
            state.open_positions = len(response.position)
            state.pending_orders = len(response.order)
            _send_symbol_list()
            return

        if payload_type == ProtoOASymbolsListRes().payloadType:
            response = Protobuf.extract(message)
            wanted = {_norm(name): name for name in _targets()}
            matches: dict[str, tuple[int, str]] = {}
            for symbol in response.symbol:
                if not bool(getattr(symbol, "enabled", True)):
                    continue
                name = str(getattr(symbol, "symbolName", "") or "")
                normalized = _norm(name)
                if normalized in wanted and normalized not in matches:
                    matches[normalized] = (int(symbol.symbolId), name)
            if not matches:
                _fail(f"None of Petra's probe symbols were found: {_targets()}")
                return
            ids = []
            for normalized in wanted:
                if normalized in matches:
                    symbol_id, broker_name = matches[normalized]
                    state.light_by_id[symbol_id] = {
                        "requested_name": wanted[normalized],
                        "broker_name": broker_name,
                    }
                    ids.append(symbol_id)
            _send_symbol_details(ids)
            return

        if payload_type == ProtoOASymbolByIdRes().payloadType:
            response = Protobuf.extract(message)
            for symbol in response.symbol:
                symbol_id = int(symbol.symbolId)
                if symbol_id in state.light_by_id:
                    state.full_by_id[symbol_id] = symbol
                    state.margin_queue.append(symbol_id)
            if not state.margin_queue:
                _fail("cTrader returned no full metadata for matched symbols")
                return
            _send_next_margin()
            return

        if payload_type == ProtoOAExpectedMarginRes().payloadType:
            response = Protobuf.extract(message)
            symbol_id = state.margin_current
            if symbol_id is None or symbol_id not in state.full_by_id:
                _fail("Unexpected expected-margin response")
                return
            if not response.margin:
                _fail(f"No expected margin returned for symbol {symbol_id}")
                return

            full = state.full_by_id[symbol_id]
            light = state.light_by_id[symbol_id]
            margin = response.margin[0]
            money_digits = int(getattr(response, "moneyDigits", state.money_digits) or state.money_digits)
            divisor = 10 ** money_digits
            buy_margin = float(margin.buyMargin) / divisor
            sell_margin = float(margin.sellMargin) / divisor
            assessment = assess_candidate(
                balance_usd=state.balance_usd,
                buy_margin_usd=buy_margin,
                sell_margin_usd=sell_margin,
                open_positions=state.open_positions,
                pending_orders=state.pending_orders,
            )
            state.results.append({
                "symbol_id": symbol_id,
                "requested_name": light["requested_name"],
                "broker_name": light["broker_name"],
                "min_volume_cents": int(getattr(full, "minVolume", 0) or 0),
                "step_volume_cents": int(getattr(full, "stepVolume", 0) or 0),
                "max_volume_cents": int(getattr(full, "maxVolume", 0) or 0),
                "min_volume_units": float(getattr(full, "minVolume", 0) or 0) / 100.0,
                "step_volume_units": float(getattr(full, "stepVolume", 0) or 0) / 100.0,
                "buy_margin_usd": round(buy_margin, money_digits),
                "sell_margin_usd": round(sell_margin, money_digits),
                "conservative_margin_usd": round(assessment.conservative_margin_usd, money_digits),
                "pilot_margin_limit_usd": round(assessment.margin_limit_usd, money_digits),
                "eligible": assessment.eligible,
                "reason": assessment.reason,
                "short_selling_enabled": bool(getattr(full, "enableShortSelling", False)),
            })
            state.margin_current = None
            _send_next_margin()
            return

    except Exception as exc:
        _fail(f"cTrader phase2 parser failed: {type(exc).__name__}: {exc}")


def _connected(_client) -> None:
    log.info("Connected to cTrader %s endpoint", settings.environment)
    req = ProtoOAApplicationAuthReq()
    req.clientId = settings.client_id
    req.clientSecret = settings.client_secret
    _send(req)


def _disconnected(_client, reason) -> None:
    if not state.failed and not state.summary.get("authenticated"):
        text = getattr(reason, "getErrorMessage", lambda: str(reason))()
        _fail(f"cTrader disconnected before Phase 2 probe completed: {text}")


def main() -> int:
    global settings, client
    try:
        settings = load_ctrader_settings()
    except CTraderSettingsError as exc:
        print(json.dumps({"broker": "ctrader", "mode": "phase2_read_only_probe", "error": str(exc)}))
        return 2

    # Phase 2 remains read-only regardless of account endpoint.
    if not settings.dry_run or settings.armed or settings.can_submit:
        print(json.dumps({"broker": "ctrader", "mode": "phase2_read_only_probe", "error": "Phase 2 probe requires dry-run=true and pilot unarmed"}))
        return 2

    host = EndPoints.PROTOBUF_LIVE_HOST if settings.environment == "live" else EndPoints.PROTOBUF_DEMO_HOST
    client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(_connected)
    client.setDisconnectedCallback(_disconnected)
    client.setMessageReceivedCallback(_on_message)
    client.startService()

    reactor.callLater(45, lambda: _fail("cTrader Phase 2 probe timed out"))
    reactor.run()

    print(json.dumps(state.summary, sort_keys=True))
    return 1 if state.failed else 0


if __name__ == "__main__":
    sys.exit(main())

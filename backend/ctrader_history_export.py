"""Read-only historical US500 M5 exporter for Petra cTrader DEMO.

Outputs CSV to stdout with: time,open,high,low,close.
No trading/order messages are imported or sent.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
from twisted.internet import reactor

from ctrader_settings import CTraderSettingsError, load_ctrader_settings

TARGET_SYMBOL = (os.environ.get("PETRA_HISTORY_SYMBOL") or "US500").strip()
DAYS = max(90, min(200, int(os.environ.get("PETRA_HISTORY_DAYS") or "180")))
WINDOW_DAYS = 7


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


class State:
    def __init__(self):
        self.failed = False
        self.symbol_id = None
        self.rows = {}
        self.window_end_ms = int(time.time() * 1000)
        self.start_ms = self.window_end_ms - DAYS * 24 * 60 * 60 * 1000


state = State()
settings = None
client = None


def fail(message: str) -> None:
    if state.failed:
        return
    state.failed = True
    print(json.dumps({"status":"error","error":message}), file=sys.stderr, flush=True)
    if reactor.running:
        reactor.callLater(0, reactor.stop)


def send(req) -> None:
    d = client.send(req, responseTimeoutInSeconds=25)
    d.addErrback(lambda f: fail(getattr(f, "getErrorMessage", lambda: str(f))()))


def account_list():
    req=ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken=settings.access_token
    send(req)


def account_auth():
    req=ProtoOAAccountAuthReq()
    req.ctidTraderAccountId=settings.expected_account_id
    req.accessToken=settings.access_token
    send(req)


def symbols():
    req=ProtoOASymbolsListReq()
    req.ctidTraderAccountId=settings.expected_account_id
    req.includeArchivedSymbols=False
    send(req)


def next_window():
    if state.window_end_ms <= state.start_ms:
        reactor.callLater(0, reactor.stop)
        return
    window_start=max(state.start_ms, state.window_end_ms - WINDOW_DAYS*24*60*60*1000)
    req=ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId=settings.expected_account_id
    req.symbolId=state.symbol_id
    req.period=ProtoOATrendbarPeriod.Value("M5")
    req.count=2500
    req.fromTimestamp=window_start
    req.toTimestamp=state.window_end_ms
    state.window_end_ms=window_start-1
    send(req)


def on_message(_client, message):
    try:
        pt=message.payloadType
        if pt==ProtoOAApplicationAuthRes().payloadType:
            account_list(); return
        if pt==ProtoOAGetAccountListByAccessTokenRes().payloadType:
            res=Protobuf.extract(message)
            ids=[int(a.ctidTraderAccountId) for a in res.ctidTraderAccount]
            if settings.expected_account_id not in ids:
                fail("Expected demo account is not authorized"); return
            account_auth(); return
        if pt==ProtoOAAccountAuthRes().payloadType:
            res=Protobuf.extract(message)
            if int(res.ctidTraderAccountId)!=settings.expected_account_id:
                fail("Authenticated account mismatch"); return
            symbols(); return
        if pt==ProtoOASymbolsListRes().payloadType:
            res=Protobuf.extract(message)
            target=_norm(TARGET_SYMBOL)
            for sym in res.symbol:
                name=str(getattr(sym,"symbolName","") or "")
                if bool(getattr(sym,"enabled",True)) and _norm(name)==target:
                    state.symbol_id=int(sym.symbolId); break
            if state.symbol_id is None:
                fail(f"{TARGET_SYMBOL} unavailable"); return
            next_window(); return
        if pt==ProtoOAGetTrendbarsRes().payloadType:
            res=Protobuf.extract(message)
            for bar in res.trendbar:
                low=float(bar.low)
                ts_min=int(getattr(bar,"utcTimestampInMinutes",0) or 0)
                if not ts_min:
                    continue
                state.rows[ts_min]=(
                    (low+float(bar.deltaOpen))/100000.0,
                    (low+float(bar.deltaHigh))/100000.0,
                    low/100000.0,
                    (low+float(bar.deltaClose))/100000.0,
                )
            next_window(); return
    except Exception as exc:
        fail(f"parser failed: {type(exc).__name__}: {exc}")


def connected(_client):
    req=ProtoOAApplicationAuthReq()
    req.clientId=settings.client_id
    req.clientSecret=settings.client_secret
    send(req)


def disconnected(_client, reason):
    if not state.failed and reactor.running:
        fail(getattr(reason,"getErrorMessage",lambda: str(reason))())


def main():
    global settings, client
    try:
        settings=load_ctrader_settings()
    except CTraderSettingsError as exc:
        print(json.dumps({"status":"error","error":str(exc)}), file=sys.stderr)
        return 2
    if settings.environment!="demo" or not settings.dry_run or settings.armed or settings.can_submit:
        print(json.dumps({"status":"blocked","error":"History export requires cTrader demo dry-run, pilot unarmed, execution disabled"}), file=sys.stderr)
        return 2
    client=Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.callLater(150, lambda: fail("history export timed out"))
    reactor.run()
    if state.failed:
        return 1
    out=io.StringIO()
    w=csv.writer(out, lineterminator="\n")
    w.writerow(["time","open","high","low","close"])
    for ts_min in sorted(state.rows):
        o,h,l,c=state.rows[ts_min]
        iso=datetime.fromtimestamp(ts_min*60,tz=timezone.utc).isoformat().replace("+00:00","Z")
        w.writerow([iso,f"{o:.5f}",f"{h:.5f}",f"{l:.5f}",f"{c:.5f}"])
    sys.stdout.write(out.getvalue())
    return 0


if __name__=="__main__":
    raise SystemExit(main())

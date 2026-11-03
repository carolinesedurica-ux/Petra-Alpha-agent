"""Read-only historical price exporter for Petra cTrader DEMO (default: US500, M5).

Outputs CSV to stdout with: time,open,high,low,close.
Set PETRA_HISTORY_SYMBOL and PETRA_HISTORY_PERIOD (M5, M15, M30, H1, H4, D1) for other markets/timeframes,
or PETRA_HISTORY_LIST_SYMBOLS=1 to print the symbols the demo account can trade instead.
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
MAX_DAYS = 6000
DAYS = max(90, min(MAX_DAYS, int(os.environ.get("PETRA_HISTORY_DAYS") or "180")))
# Export an older slice: window ends this many days before now (default 0 = up to now).
END_DAYS_AGO = max(0, min(3650, int(os.environ.get("PETRA_HISTORY_END_DAYS_AGO") or "0")))
# Overall time limit. The default matches the old fixed 150 s; raise it for multi-year exports.
TIMEOUT_SECONDS = max(60, min(3600, int(os.environ.get("PETRA_HISTORY_TIMEOUT_SECONDS") or "150")))
PERIOD = (os.environ.get("PETRA_HISTORY_PERIOD") or "M5").strip().upper()
LIST_SYMBOLS = (os.environ.get("PETRA_HISTORY_LIST_SYMBOLS") or "").strip().lower() in {"1", "true", "yes", "on"}
# Calendar days requested per call, sized so one call stays under the 2500-bar request limit.
# M5 keeps its original 7 days, so existing behaviour is unchanged.
WINDOW_DAYS_BY_PERIOD = {"M5": 7, "M15": 25, "M30": 50, "H1": 100, "H4": 400, "D1": 2400}
WINDOW_DAYS = WINDOW_DAYS_BY_PERIOD.get(PERIOD, 7)
DAY_MS = 24 * 60 * 60 * 1000


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


class State:
    def __init__(self):
        self.failed = False
        self.symbol_id = None
        self.listing = []
        self.rows = {}
        self.window_end_ms = int(time.time() * 1000) - END_DAYS_AGO * DAY_MS
        self.start_ms = self.window_end_ms - DAYS * DAY_MS


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
    req.period=ProtoOATrendbarPeriod.Value(PERIOD)
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
            if LIST_SYMBOLS:
                state.listing = sorted(
                    (str(getattr(sym, "symbolName", "") or ""), int(sym.symbolId),
                     str(getattr(sym, "description", "") or ""))
                    for sym in res.symbol if bool(getattr(sym, "enabled", True)))
                reactor.callLater(0, reactor.stop); return
            target=_norm(TARGET_SYMBOL)
            for sym in res.symbol:
                name=str(getattr(sym,"symbolName","") or "")
                if bool(getattr(sym,"enabled",True)) and _norm(name)==target:
                    state.symbol_id=int(sym.symbolId); break
            if state.symbol_id is None:
                near = sorted(str(getattr(s_, "symbolName", "") or "") for s_ in res.symbol
                              if bool(getattr(s_, "enabled", True)) and target[:3] and target[:3] in _norm(str(getattr(s_, "symbolName", "") or "")))[:15]
                fail(f"{TARGET_SYMBOL} unavailable" + (f"; similar enabled symbols: {', '.join(near)}" if near else
                     "; run with PETRA_HISTORY_LIST_SYMBOLS=1 to see what the account offers")); return
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
    if PERIOD not in WINDOW_DAYS_BY_PERIOD:
        print(json.dumps({"status":"error","error":f"PETRA_HISTORY_PERIOD must be one of {sorted(WINDOW_DAYS_BY_PERIOD)}, got {PERIOD!r}"}), file=sys.stderr)
        return 2
    client=Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.callLater(TIMEOUT_SECONDS, lambda: fail("history export timed out"))
    reactor.run()
    if state.failed:
        return 1
    if LIST_SYMBOLS:
        w = csv.writer(sys.stdout, lineterminator="\n")
        w.writerow(["symbol", "id", "description"])
        w.writerows(state.listing)
        return 0
    if state.rows:
        first, last = min(state.rows) * 60, max(state.rows) * 60
        print(json.dumps({
            "status": "ok", "symbol": TARGET_SYMBOL, "bars": len(state.rows),
            "first": datetime.fromtimestamp(first, tz=timezone.utc).isoformat(),
            "last": datetime.fromtimestamp(last, tz=timezone.utc).isoformat(),
            "period": PERIOD, "requested_days": DAYS, "end_days_ago": END_DAYS_AGO,
        }), file=sys.stderr, flush=True)
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

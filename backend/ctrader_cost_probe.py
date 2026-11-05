"""Read-only trading-cost probe for Petra cTrader DEMO.

For a list of symbols it reports, as CSV on stdout:
  * the broker's specification: swap long/short and how they are calculated, triple-swap day,
    commission, lot size, volume step, digits/pip position, trading mode;
  * measured live spreads: it subscribes to quotes for a short time and records ask - bid.

Run it at several times of day (Asian night, London open, US open) because spreads move with liquidity.
It authenticates the whitelisted demo account only, and imports no order messages.

Environment:
  PETRA_COST_SYMBOLS          comma-separated names (default: the research basket)
  PETRA_COST_SAMPLE_SECONDS   how long to listen to quotes, 20-900 (default 90)
  PETRA_COST_TIMEOUT_SECONDS  overall limit, 60-1800 (default 240)
"""
from __future__ import annotations

import csv
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAccountAuthRes,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOAGetAccountListByAccessTokenRes,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASubscribeSpotsRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOACommissionType,
    ProtoOADayOfWeek,
    ProtoOASwapCalculationType,
    ProtoOATradingMode,
)
from twisted.internet import reactor

from ctrader_settings import CTraderSettingsError, load_ctrader_settings

DEFAULT_SYMBOLS = "XAUUSD,XAGUSD,SpotCrude,NatGas,US500,NAS100,GER40,UK100,US30,SpotBrent"
PRICE_SCALE = 100000.0  # cTrader quotes are integers scaled by 1e5
COLUMNS = [
    "run_utc", "symbol", "symbol_id", "description", "trading_mode", "digits", "pip_position",
    "lot_size", "min_volume", "step_volume", "short_selling",
    "swap_long", "swap_short", "swap_calc_type", "swap_period", "swap_time_min", "swap_rollover_3days",
    "charge_swap_weekends", "commission", "commission_type", "min_commission",
    "samples", "bid_last", "ask_last", "mid", "spread_median", "spread_p10", "spread_p90", "spread_bps_median",
]


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(os.environ.get(name) or default)))


SYMBOLS = [s.strip() for s in (os.environ.get("PETRA_COST_SYMBOLS") or DEFAULT_SYMBOLS).split(",") if s.strip()]
SAMPLE_SECONDS = _env_int("PETRA_COST_SAMPLE_SECONDS", 90, 20, 900)
TIMEOUT_SECONDS = _env_int("PETRA_COST_TIMEOUT_SECONDS", 240, 60, 1800)


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _enum_name(enum_type, value: int) -> str:
    try:
        return enum_type.Name(int(value))
    except ValueError:
        return f"UNKNOWN({value})"


# --------------------------------------------------------------------------- pure helpers
def decode_symbol(sym) -> dict:
    """Raw broker specification fields (units are the API's: swap in pips or % per the calc type)."""
    return {
        "trading_mode": _enum_name(ProtoOATradingMode, getattr(sym, "tradingMode", 0)),
        "digits": int(sym.digits),
        "pip_position": int(sym.pipPosition),
        "lot_size": int(getattr(sym, "lotSize", 0) or 0),
        "min_volume": int(getattr(sym, "minVolume", 0) or 0),
        "step_volume": int(getattr(sym, "stepVolume", 0) or 0),
        "short_selling": bool(getattr(sym, "enableShortSelling", False)),
        "swap_long": float(getattr(sym, "swapLong", 0.0) or 0.0),
        "swap_short": float(getattr(sym, "swapShort", 0.0) or 0.0),
        "swap_calc_type": _enum_name(ProtoOASwapCalculationType, getattr(sym, "swapCalculationType", 0)),
        "swap_period": int(getattr(sym, "swapPeriod", 0) or 0),
        "swap_time_min": int(getattr(sym, "swapTime", 0) or 0),
        "swap_rollover_3days": _enum_name(ProtoOADayOfWeek, getattr(sym, "swapRollover3Days", 0)),
        "charge_swap_weekends": bool(getattr(sym, "chargeSwapAtWeekends", False)),
        "commission": int(getattr(sym, "commission", 0) or 0),
        "commission_type": _enum_name(ProtoOACommissionType, getattr(sym, "commissionType", 1)),
        "min_commission": int(getattr(sym, "minCommission", 0) or 0),
    }


class SpotBook:
    """Keeps the last bid/ask per symbol and records the spread whenever both sides are known.

    cTrader may send updates carrying only one side, so each side is remembered between events.
    """

    def __init__(self) -> None:
        self.bid: dict[int, float] = {}
        self.ask: dict[int, float] = {}
        self.spreads: dict[int, list[float]] = {}

    def update(self, symbol_id: int, bid_raw: int | None, ask_raw: int | None) -> None:
        if bid_raw:
            self.bid[symbol_id] = bid_raw / PRICE_SCALE
        if ask_raw:
            self.ask[symbol_id] = ask_raw / PRICE_SCALE
        b, a = self.bid.get(symbol_id), self.ask.get(symbol_id)
        if b and a and a > b:  # crossed/locked quotes are ignored, not treated as zero spread
            self.spreads.setdefault(symbol_id, []).append(a - b)

    def stats(self, symbol_id: int) -> dict:
        return spread_stats(self.spreads.get(symbol_id, []), self.bid.get(symbol_id), self.ask.get(symbol_id))


def _pct(sorted_values: list[float], p: float) -> float:
    return sorted_values[min(len(sorted_values) - 1, int(p * len(sorted_values)))]


def spread_stats(spreads: list[float], bid: float | None, ask: float | None) -> dict:
    if not spreads or not bid or not ask:
        return {"samples": 0, "bid_last": bid, "ask_last": ask, "mid": None, "spread_median": None,
                "spread_p10": None, "spread_p90": None, "spread_bps_median": None}
    s = sorted(spreads)
    mid = (bid + ask) / 2.0
    med = statistics.median(s)
    return {"samples": len(s), "bid_last": bid, "ask_last": ask, "mid": mid, "spread_median": med,
            "spread_p10": _pct(s, 0.10), "spread_p90": _pct(s, 0.90), "spread_bps_median": med / mid * 1e4}


def build_rows(run_utc: str, resolved: dict[int, tuple[str, str]], specs: dict[int, dict], book: SpotBook) -> list[dict]:
    rows = []
    for sid, (name, description) in sorted(resolved.items(), key=lambda kv: kv[1][0]):
        row = {"run_utc": run_utc, "symbol": name, "symbol_id": sid, "description": description}
        row.update(specs.get(sid, {}))
        row.update(book.stats(sid))
        rows.append(row)
    return rows


def write_csv(rows: list[dict], out) -> None:
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else (f"{r[k]:.6f}" if isinstance(r.get(k), float) else r[k])) for k in COLUMNS})


# --------------------------------------------------------------------------- broker session
class State:
    def __init__(self) -> None:
        self.failed = False
        self.resolved: dict[int, tuple[str, str]] = {}
        self.missing: list[str] = []
        self.specs: dict[int, dict] = {}
        self.book = SpotBook()
        self.run_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


state = State()
settings = None
client = None


def fail(message: str) -> None:
    if state.failed:
        return
    state.failed = True
    print(json.dumps({"status": "error", "error": message}), file=sys.stderr, flush=True)
    if reactor.running:
        reactor.callLater(0, reactor.stop)


def send(req) -> None:
    d = client.send(req, responseTimeoutInSeconds=25)
    d.addErrback(lambda f: fail(getattr(f, "getErrorMessage", lambda: str(f))()))


def on_message(_client, message) -> None:
    try:
        pt = message.payloadType
        if pt == ProtoOAApplicationAuthRes().payloadType:
            req = ProtoOAGetAccountListByAccessTokenReq()
            req.accessToken = settings.access_token
            send(req); return
        if pt == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            res = Protobuf.extract(message)
            if settings.expected_account_id not in [int(a.ctidTraderAccountId) for a in res.ctidTraderAccount]:
                fail("Expected demo account is not authorized"); return
            req = ProtoOAAccountAuthReq()
            req.ctidTraderAccountId = settings.expected_account_id
            req.accessToken = settings.access_token
            send(req); return
        if pt == ProtoOAAccountAuthRes().payloadType:
            res = Protobuf.extract(message)
            if int(res.ctidTraderAccountId) != settings.expected_account_id:
                fail("Authenticated account mismatch"); return
            req = ProtoOASymbolsListReq()
            req.ctidTraderAccountId = settings.expected_account_id
            req.includeArchivedSymbols = False
            send(req); return
        if pt == ProtoOASymbolsListRes().payloadType:
            res = Protobuf.extract(message)
            wanted = {_norm(s): s for s in SYMBOLS}
            for sym in res.symbol:
                name = str(getattr(sym, "symbolName", "") or "")
                if bool(getattr(sym, "enabled", True)) and _norm(name) in wanted:
                    state.resolved[int(sym.symbolId)] = (name, str(getattr(sym, "description", "") or ""))
            found = {_norm(n) for n, _ in state.resolved.values()}
            state.missing = [s for s in SYMBOLS if _norm(s) not in found]
            if not state.resolved:
                fail("none of the requested symbols are available on this account"); return
            req = ProtoOASymbolByIdReq()
            req.ctidTraderAccountId = settings.expected_account_id
            req.symbolId.extend(sorted(state.resolved))
            send(req); return
        if pt == ProtoOASymbolByIdRes().payloadType:
            res = Protobuf.extract(message)
            for sym in res.symbol:
                state.specs[int(sym.symbolId)] = decode_symbol(sym)
            req = ProtoOASubscribeSpotsReq()
            req.ctidTraderAccountId = settings.expected_account_id
            req.symbolId.extend(sorted(state.resolved))
            send(req); return
        if pt == ProtoOASubscribeSpotsRes().payloadType:
            reactor.callLater(SAMPLE_SECONDS, reactor.stop)  # listen, then finish; closing the connection unsubscribes
            return
        if pt == ProtoOASpotEvent().payloadType:
            ev = Protobuf.extract(message)
            sid = int(ev.symbolId)
            if sid in state.resolved:
                state.book.update(sid, int(ev.bid) if ev.HasField("bid") else None,
                                  int(ev.ask) if ev.HasField("ask") else None)
    except Exception as exc:  # noqa: BLE001
        fail(f"parser failed: {type(exc).__name__}: {exc}")


def connected(_client) -> None:
    req = ProtoOAApplicationAuthReq()
    req.clientId = settings.client_id
    req.clientSecret = settings.client_secret
    send(req)


def disconnected(_client, reason) -> None:
    if not state.failed and reactor.running:
        fail(getattr(reason, "getErrorMessage", lambda: str(reason))())


def main() -> int:
    global settings, client
    try:
        settings = load_ctrader_settings()
    except CTraderSettingsError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    if settings.environment != "demo" or not settings.dry_run or settings.armed or settings.can_submit:
        print(json.dumps({"status": "blocked", "error": "Cost probe requires cTrader demo dry-run, pilot unarmed, execution disabled"}),
              file=sys.stderr)
        return 2
    client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(connected)
    client.setDisconnectedCallback(disconnected)
    client.setMessageReceivedCallback(on_message)
    client.startService()
    reactor.callLater(TIMEOUT_SECONDS, lambda: fail("cost probe timed out"))
    reactor.run()
    if state.failed:
        return 1
    rows = build_rows(state.run_utc, state.resolved, state.specs, state.book)
    write_csv(rows, sys.stdout)
    print(json.dumps({"status": "ok", "symbols": len(rows), "missing": state.missing,
                      "sample_seconds": SAMPLE_SECONDS,
                      "symbols_without_quotes": [r["symbol"] for r in rows if not r.get("samples")]}),
          file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

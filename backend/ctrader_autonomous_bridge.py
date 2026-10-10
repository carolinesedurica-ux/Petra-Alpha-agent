"""Petra autonomous cTrader analysis bridge (demo / shadow only).

This module connects to the explicitly whitelisted cTrader demo account, reads
US500 broker metadata, expected margin, recent M5 trendbars, and current account
state, then produces a deterministic BUY / SELL / NO_TRADE market verdict.

It intentionally imports no cTrader order-submission messages and cannot place,
modify, or close broker orders. The output is designed to feed Petra's existing
risk/decision architecture before a separately reviewed demo execution adapter is
introduced.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
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
    ProtoOAGetTrendbarsReq,
    ProtoOAGetTrendbarsRes,
    ProtoOAReconcileReq,
    ProtoOAReconcileRes,
    ProtoOASymbolByIdReq,
    ProtoOASymbolByIdRes,
    ProtoOASymbolsListReq,
    ProtoOASymbolsListRes,
    ProtoOATraderReq,
    ProtoOATraderRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
from twisted.internet import reactor

from ctrader_pilot_risk import assess_candidate
from ctrader_settings import CTraderSettingsError, load_ctrader_settings

log = logging.getLogger("petra.ctrader.autonomous")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

TARGET_SYMBOL = (os.environ.get("PETRA_CTRADER_AUTONOMOUS_SYMBOL") or "US500").strip()
BAR_COUNT = max(40, min(120, int(os.environ.get("PETRA_CTRADER_BAR_COUNT") or "72")))
CONFIDENCE_FLOOR = max(0.50, min(0.90, float(os.environ.get("PETRA_CTRADER_CONFIDENCE_FLOOR") or "0.68")))


def _norm(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    out = values[0]
    for value in values[1:]:
        out = alpha * value + (1.0 - alpha) * out
    return out


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    for i in range(len(values) - period, len(values)):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    if losses <= 1e-12:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(bars: list[dict[str, float]], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(bars)):
        cur = bars[i]
        prev_close = bars[i - 1]["close"]
        trs.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev_close),
            abs(cur["low"] - prev_close),
        ))
    sample = trs[-period:]
    return sum(sample) / len(sample) if sample else 0.0


def _analyze(bars: list[dict[str, float]]) -> dict[str, Any]:
    closes = [b["close"] for b in bars]
    fast = _ema(closes[-40:], 9)
    slow = _ema(closes[-50:], 21)
    rsi = _rsi(closes, 14)
    atr = _atr(bars, 14)
    last = closes[-1]
    momentum_3 = (last / closes[-4] - 1.0) if len(closes) >= 4 and closes[-4] else 0.0
    momentum_12 = (last / closes[-13] - 1.0) if len(closes) >= 13 and closes[-13] else 0.0
    trend_gap = fast - slow
    trend_strength = abs(trend_gap) / atr if atr > 0 else 0.0

    bull_checks = {
        "ema_alignment": fast > slow,
        "rsi_zone": 52.0 <= rsi <= 72.0,
        "momentum_3": momentum_3 > 0,
        "momentum_12": momentum_12 > 0,
        "trend_strength": trend_strength >= 0.18,
    }
    bear_checks = {
        "ema_alignment": fast < slow,
        "rsi_zone": 28.0 <= rsi <= 48.0,
        "momentum_3": momentum_3 < 0,
        "momentum_12": momentum_12 < 0,
        "trend_strength": trend_strength >= 0.18,
    }
    bull_score = sum(bool(v) for v in bull_checks.values())
    bear_score = sum(bool(v) for v in bear_checks.values())

    direction = "NO_TRADE"
    selected = {}
    score = max(bull_score, bear_score)
    if bull_score >= 4 and bull_score > bear_score:
        direction = "BUY"
        selected = bull_checks
    elif bear_score >= 4 and bear_score > bull_score:
        direction = "SELL"
        selected = bear_checks

    confidence = 0.0 if direction == "NO_TRADE" else min(
        0.95,
        0.48
        + score * 0.075
        + min(trend_strength, 1.5) * 0.06
        + min(abs(momentum_12) * 100.0, 1.0) * 0.03,
    )
    if confidence < CONFIDENCE_FLOOR:
        direction = "NO_TRADE"

    stop_distance = max(atr * 1.25, last * 0.0008) if atr > 0 else last * 0.001
    target_distance = stop_distance * 1.6
    stop_loss = None
    take_profit = None
    if direction == "BUY":
        stop_loss = last - stop_distance
        take_profit = last + target_distance
    elif direction == "SELL":
        stop_loss = last + stop_distance
        take_profit = last - target_distance

    reason = "No sufficiently aligned setup; preserve capital and wait."
    if direction != "NO_TRADE":
        reason = (
            f"{direction} setup: EMA trend, momentum and RSI alignment passed "
            f"{score}/5 checks with trend strength {trend_strength:.2f}."
        )

    return {
        "decision": direction,
        "confidence": round(confidence, 3),
        "confidence_floor": CONFIDENCE_FLOOR,
        "reason": reason,
        "price": round(last, 5),
        "ema_9": round(fast, 5),
        "ema_21": round(slow, 5),
        "rsi_14": round(rsi, 2),
        "atr_14": round(atr, 5),
        "momentum_3_pct": round(momentum_3 * 100.0, 4),
        "momentum_12_pct": round(momentum_12 * 100.0, 4),
        "trend_strength": round(trend_strength, 3),
        "checks": selected,
        "suggested_stop_loss": round(stop_loss, 5) if stop_loss is not None else None,
        "suggested_take_profit": round(take_profit, 5) if take_profit is not None else None,
        "risk_reward": 1.6 if direction != "NO_TRADE" else None,
    }


class State:
    def __init__(self) -> None:
        self.failed = False
        self.money_digits = 2
        self.balance = 0.0
        self.open_positions = 0
        self.pending_orders = 0
        self.symbol_id: int | None = None
        self.symbol_name = ""
        self.symbol = None
        self.margin: dict[str, float] = {}
        self.summary: dict[str, Any] = {
            "broker": "ctrader",
            "mode": "autonomous_shadow_analysis",
            "symbol": TARGET_SYMBOL,
            "execution_enabled": False,
            "orders_enabled": False,
        }


state = State()
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


def _trader() -> None:
    req = ProtoOATraderReq()
    req.ctidTraderAccountId = settings.expected_account_id
    _send(req)


def _reconcile() -> None:
    req = ProtoOAReconcileReq()
    req.ctidTraderAccountId = settings.expected_account_id
    _send(req)


def _symbols() -> None:
    req = ProtoOASymbolsListReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.includeArchivedSymbols = False
    _send(req)


def _symbol_details() -> None:
    req = ProtoOASymbolByIdReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.symbolId.append(state.symbol_id)
    _send(req)


def _expected_margin() -> None:
    min_volume = int(getattr(state.symbol, "minVolume", 0) or 0)
    if min_volume <= 0:
        _fail("US500 broker minimum volume is unavailable")
        return
    req = ProtoOAExpectedMarginReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.symbolId = state.symbol_id
    req.volume.append(min_volume)
    _send(req)


def _trendbars() -> None:
    req = ProtoOAGetTrendbarsReq()
    req.ctidTraderAccountId = settings.expected_account_id
    req.symbolId = state.symbol_id
    req.period = ProtoOATrendbarPeriod.Value("M5")
    req.count = BAR_COUNT
    req.toTimestamp = int(time.time() * 1000)
    req.fromTimestamp = req.toTimestamp - (BAR_COUNT + 20) * 5 * 60 * 1000
    _send(req)


def _on_message(_client, message) -> None:
    try:
        pt = message.payloadType
        if pt == ProtoOAApplicationAuthRes().payloadType:
            _account_list(); return
        if pt == ProtoOAGetAccountListByAccessTokenRes().payloadType:
            res = Protobuf.extract(message)
            ids = [int(a.ctidTraderAccountId) for a in res.ctidTraderAccount]
            if settings.expected_account_id not in ids:
                _fail("Expected cTrader account is not authorized by token"); return
            _account_auth(); return
        if pt == ProtoOAAccountAuthRes().payloadType:
            res = Protobuf.extract(message)
            if int(res.ctidTraderAccountId) != settings.expected_account_id:
                _fail("Authenticated account does not match Petra whitelist"); return
            _trader(); return
        if pt == ProtoOATraderRes().payloadType:
            res = Protobuf.extract(message)
            trader = res.trader
            state.money_digits = int(getattr(trader, "moneyDigits", 2) or 2)
            state.balance = float(trader.balance) / (10 ** state.money_digits)
            _reconcile(); return
        if pt == ProtoOAReconcileRes().payloadType:
            res = Protobuf.extract(message)
            state.open_positions = len(res.position)
            state.pending_orders = len(res.order)
            _symbols(); return
        if pt == ProtoOASymbolsListRes().payloadType:
            res = Protobuf.extract(message)
            target = _norm(TARGET_SYMBOL)
            for symbol in res.symbol:
                name = str(getattr(symbol, "symbolName", "") or "")
                if bool(getattr(symbol, "enabled", True)) and _norm(name) == target:
                    state.symbol_id = int(symbol.symbolId)
                    state.symbol_name = name
                    break
            if state.symbol_id is None:
                _fail(f"{TARGET_SYMBOL} is unavailable on the authorized cTrader account"); return
            _symbol_details(); return
        if pt == ProtoOASymbolByIdRes().payloadType:
            res = Protobuf.extract(message)
            if not res.symbol:
                _fail("cTrader returned no full symbol metadata"); return
            state.symbol = res.symbol[0]
            _expected_margin(); return
        if pt == ProtoOAExpectedMarginRes().payloadType:
            res = Protobuf.extract(message)
            if not res.margin:
                _fail("cTrader returned no expected margin"); return
            md = int(getattr(res, "moneyDigits", state.money_digits) or state.money_digits)
            divisor = 10 ** md
            m = res.margin[0]
            buy_margin = float(m.buyMargin) / divisor
            sell_margin = float(m.sellMargin) / divisor
            assessment = assess_candidate(
                balance_usd=state.balance,
                buy_margin_usd=buy_margin,
                sell_margin_usd=sell_margin,
                open_positions=state.open_positions,
                pending_orders=state.pending_orders,
            )
            state.margin = {
                "buy_margin_usd": round(buy_margin, md),
                "sell_margin_usd": round(sell_margin, md),
                "conservative_margin_usd": round(assessment.conservative_margin_usd, md),
                "margin_limit_usd": round(assessment.margin_limit_usd, md),
                "risk_eligible": assessment.eligible,
                "risk_reason": assessment.reason,
            }
            _trendbars(); return
        if pt == ProtoOAGetTrendbarsRes().payloadType:
            res = Protobuf.extract(message)
            raw_bars = list(res.trendbar)
            if len(raw_bars) < 30:
                _fail(f"Only {len(raw_bars)} cTrader bars returned; need at least 30"); return
            bars: list[dict[str, float]] = []
            for bar in raw_bars:
                low_raw = float(bar.low)
                bars.append({
                    "open": (low_raw + float(bar.deltaOpen)) / 100000.0,
                    "high": (low_raw + float(bar.deltaHigh)) / 100000.0,
                    "low": low_raw / 100000.0,
                    "close": (low_raw + float(bar.deltaClose)) / 100000.0,
                    "volume": float(getattr(bar, "volume", 0) or 0),
                    "ts_minutes": int(getattr(bar, "utcTimestampInMinutes", 0) or 0),
                })
            bars.sort(key=lambda x: x["ts_minutes"])
            analysis = _analyze(bars)
            risk_ok = bool(state.margin.get("risk_eligible"))
            account_flat = state.open_positions == 0 and state.pending_orders == 0
            actionable = analysis["decision"] in {"BUY", "SELL"} and risk_ok and account_flat
            final_decision = analysis["decision"] if actionable else "NO_TRADE"
            if analysis["decision"] != "NO_TRADE" and not actionable:
                analysis["reason"] += " Risk/account gate blocked new exposure."
            state.summary.update({
                "status": "ok",
                "authenticated": True,
                "account_id": settings.expected_account_id,
                "balance_usd": round(state.balance, state.money_digits),
                "open_positions": state.open_positions,
                "pending_orders": state.pending_orders,
                "broker_symbol": state.symbol_name,
                "symbol_id": state.symbol_id,
                "bar_period": "M5",
                "bar_count": len(bars),
                "minimum_volume_units": float(getattr(state.symbol, "minVolume", 0) or 0) / 100.0,
                "margin": state.margin,
                "analysis": analysis,
                "final_decision": final_decision,
                "shadow_actionable": actionable,
                "execution_enabled": False,
                "orders_enabled": False,
            })
            reactor.callLater(0, reactor.stop)
            return
    except Exception as exc:
        _fail(f"cTrader autonomous parser failed: {type(exc).__name__}: {exc}")


def _connected(_client) -> None:
    req = ProtoOAApplicationAuthReq()
    req.clientId = settings.client_id
    req.clientSecret = settings.client_secret
    _send(req)


def _disconnected(_client, reason) -> None:
    if not state.failed and state.summary.get("status") != "ok":
        text = getattr(reason, "getErrorMessage", lambda: str(reason))()
        _fail(f"cTrader disconnected before autonomous analysis completed: {text}")


def main() -> int:
    global settings, client
    try:
        settings = load_ctrader_settings()
    except CTraderSettingsError as exc:
        print(json.dumps({"broker": "ctrader", "mode": "autonomous_shadow_analysis", "status": "error", "error": str(exc)}))
        return 2

    if settings.environment != "demo" or not settings.dry_run or settings.armed or settings.can_submit:
        print(json.dumps({
            "broker": "ctrader",
            "mode": "autonomous_shadow_analysis",
            "status": "blocked",
            "error": "Autonomous analysis bridge requires cTrader demo, dry-run=true, pilot unarmed, and execution disabled",
        }))
        return 2

    host = EndPoints.PROTOBUF_DEMO_HOST
    client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
    client.setConnectedCallback(_connected)
    client.setDisconnectedCallback(_disconnected)
    client.setMessageReceivedCallback(_on_message)
    client.startService()
    reactor.callLater(50, lambda: _fail("cTrader autonomous analysis timed out"))
    reactor.run()

    print(json.dumps(state.summary, sort_keys=True))
    return 1 if state.failed else 0


if __name__ == "__main__":
    sys.exit(main())

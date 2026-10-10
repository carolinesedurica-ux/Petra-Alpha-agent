"""Autonomous cTrader paper-trading engine for Petra.

Consumes Petra's existing cTrader autonomous shadow analysis and maintains a
local simulated position state. This module never imports or sends cTrader order
submission messages and cannot place, modify, or close broker orders.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("PETRA_CTRADER_PAPER_STATE_FILE") or "/tmp/petra_ctrader_paper_state.json")
STARTING_BALANCE = float(os.environ.get("PETRA_CTRADER_PAPER_STARTING_BALANCE") or "1000")
MAX_RISK_PCT = max(0.05, min(2.0, float(os.environ.get("PETRA_CTRADER_PAPER_RISK_PCT") or "0.50")))
MAX_POSITION_NOTIONAL = max(1.0, float(os.environ.get("PETRA_CTRADER_PAPER_MAX_NOTIONAL") or "25"))


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


def _default_state() -> dict[str, Any]:
    return {
        "mode": "ctrader_paper_autonomous",
        "balance": STARTING_BALANCE,
        "equity": STARTING_BALANCE,
        "realized_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "cycles": 0,
        "position": None,
        "last_action": "INIT",
        "updated_at": None,
    }


def _load_state() -> dict[str, Any]:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return _default_state()


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, sort_keys=True))


def _close_position(state: dict[str, Any], price: float, reason: str) -> None:
    pos = state.get("position")
    if not pos:
        return
    side = pos["side"]
    entry = float(pos["entry_price"])
    qty = float(pos["quantity"])
    pnl = (price - entry) * qty if side == "BUY" else (entry - price) * qty
    state["balance"] = round(float(state.get("balance", STARTING_BALANCE)) + pnl, 6)
    state["realized_pnl"] = round(float(state.get("realized_pnl", 0.0)) + pnl, 6)
    if pnl >= 0:
        state["wins"] = int(state.get("wins", 0)) + 1
    else:
        state["losses"] = int(state.get("losses", 0)) + 1
    state["last_action"] = "CLOSE"
    state["last_close"] = {
        "side": side,
        "entry_price": entry,
        "exit_price": price,
        "quantity": qty,
        "pnl": round(pnl, 6),
        "reason": reason,
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }
    state["position"] = None


def _open_position(state: dict[str, Any], analysis: dict[str, Any], direction: str) -> None:
    signal = analysis["analysis"]
    price = float(signal["price"])
    stop = float(signal["suggested_stop_loss"])
    target = float(signal["suggested_take_profit"])
    stop_distance = abs(price - stop)
    if stop_distance <= 0:
        state["last_action"] = "NO_ACTION"
        state["last_reason"] = "Invalid stop distance"
        return

    balance = float(state.get("balance", STARTING_BALANCE))
    risk_budget = max(0.01, balance * (MAX_RISK_PCT / 100.0))
    risk_qty = risk_budget / stop_distance
    notional_qty = MAX_POSITION_NOTIONAL / price if price > 0 else 0.0
    qty = max(0.0, min(risk_qty, notional_qty))
    if qty <= 0:
        state["last_action"] = "NO_ACTION"
        state["last_reason"] = "Calculated paper quantity is zero"
        return

    state["position"] = {
        "side": direction,
        "entry_price": price,
        "quantity": round(qty, 8),
        "stop_loss": stop,
        "take_profit": target,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "symbol": analysis.get("broker_symbol") or analysis.get("symbol") or "US500",
    }
    state["last_action"] = f"OPEN_{direction}"
    state["last_reason"] = signal.get("reason")


def run_cycle() -> dict[str, Any]:
    analysis = _run_analysis()
    state = _load_state()
    state["cycles"] = int(state.get("cycles", 0)) + 1
    signal = analysis.get("analysis") or {}
    price = float(signal.get("price") or 0.0)
    decision = str(signal.get("decision") or "NO_TRADE")
    confidence = float(signal.get("confidence") or 0.0)
    floor = float(signal.get("confidence_floor") or 1.0)

    pos = state.get("position")
    if pos and price > 0:
        side = pos["side"]
        stop = float(pos["stop_loss"])
        target = float(pos["take_profit"])
        hit_stop = (side == "BUY" and price <= stop) or (side == "SELL" and price >= stop)
        hit_target = (side == "BUY" and price >= target) or (side == "SELL" and price <= target)
        opposite = (side == "BUY" and decision == "SELL") or (side == "SELL" and decision == "BUY")
        if hit_stop:
            _close_position(state, price, "paper stop-loss reached")
        elif hit_target:
            _close_position(state, price, "paper take-profit reached")
        elif opposite and confidence >= floor:
            _close_position(state, price, f"strong opposite {decision} signal")
        else:
            state["last_action"] = "HOLD"
            state["last_reason"] = "Existing paper position retained"

    if state.get("position") is None and bool(analysis.get("shadow_actionable")):
        direction = str(analysis.get("final_decision") or "NO_TRADE")
        if direction in {"BUY", "SELL"}:
            _open_position(state, analysis, direction)
        elif state.get("last_action") == "INIT":
            state["last_action"] = "NO_ACTION"
    elif state.get("position") is None and state.get("last_action") == "INIT":
        state["last_action"] = "NO_ACTION"
        state["last_reason"] = signal.get("reason") or "No actionable setup"

    pos = state.get("position")
    unrealized = 0.0
    if pos and price > 0:
        entry = float(pos["entry_price"])
        qty = float(pos["quantity"])
        unrealized = (price - entry) * qty if pos["side"] == "BUY" else (entry - price) * qty
    state["equity"] = round(float(state.get("balance", STARTING_BALANCE)) + unrealized, 6)
    state["unrealized_pnl"] = round(unrealized, 6)
    state["analysis_decision"] = decision
    state["analysis_confidence"] = confidence
    state["broker_account_id"] = analysis.get("account_id")
    state["broker_symbol"] = analysis.get("broker_symbol")
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    return {
        "status": "ok",
        "mode": "ctrader_paper_autonomous",
        "orders_enabled": False,
        "broker_execution": False,
        "analysis_decision": decision,
        "final_decision": analysis.get("final_decision"),
        "shadow_actionable": analysis.get("shadow_actionable"),
        "last_action": state.get("last_action"),
        "last_reason": state.get("last_reason"),
        "balance": state.get("balance"),
        "equity": state.get("equity"),
        "realized_pnl": state.get("realized_pnl"),
        "unrealized_pnl": state.get("unrealized_pnl"),
        "wins": state.get("wins"),
        "losses": state.get("losses"),
        "cycles": state.get("cycles"),
        "position": state.get("position"),
    }


def main() -> int:
    try:
        result = run_cycle()
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "mode": "ctrader_paper_autonomous",
            "orders_enabled": False,
            "broker_execution": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only diagnostic for cTrader relative SL/TP precision.

Runs Petra's existing autonomous shadow analysis, then converts the suggested
stop-loss and take-profit distances into cTrader relative protection units.
It reports both the raw 1/100000-unit values and values aligned to the broker
symbol's advertised price precision. This script never creates, modifies, or
closes an order.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _run_shadow_analysis() -> dict[str, Any]:
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
        raise RuntimeError("Autonomous analysis returned no JSON output")
    payload = json.loads(lines[-1])
    if completed.returncode != 0 or payload.get("status") != "ok":
        raise RuntimeError(payload.get("error") or "Autonomous analysis failed")
    return payload


def _quantum_units(symbol_digits: int) -> int:
    """Return the minimum valid increment in cTrader relative units.

    Relative SL/TP uses 1 unit = 0.00001 of price. A symbol with N displayed
    decimal places therefore moves in increments of 10**(5-N) relative units.
    """
    digits = max(0, min(5, int(symbol_digits)))
    return 10 ** (5 - digits)


def _align_relative(distance: float, symbol_digits: int) -> tuple[int, int, int]:
    raw = int(round(abs(float(distance)) * 100000.0))
    quantum = _quantum_units(symbol_digits)
    aligned = int(round(raw / quantum) * quantum)
    if aligned <= 0 and raw > 0:
        aligned = quantum
    return raw, aligned, quantum


def main() -> int:
    try:
        payload = _run_shadow_analysis()
        signal = payload.get("analysis") or {}
        price = float(signal.get("price") or 0.0)
        stop = float(signal.get("suggested_stop_loss") or 0.0)
        target = float(signal.get("suggested_take_profit") or 0.0)
        digits = int(payload.get("symbol_digits") or os.environ.get("PETRA_CTRADER_SYMBOL_DIGITS") or 2)

        if price <= 0 or stop <= 0 or target <= 0:
            result = {
                "status": "ok",
                "mode": "read_only_precision_check",
                "decision": signal.get("decision", "NO_TRADE"),
                "actionable": False,
                "reason": "Current shadow analysis has no executable SL/TP suggestion",
                "orders_enabled": False,
            }
            print(json.dumps(result, sort_keys=True))
            return 0

        stop_distance = abs(price - stop)
        target_distance = abs(target - price)
        stop_raw, stop_aligned, quantum = _align_relative(stop_distance, digits)
        target_raw, target_aligned, _ = _align_relative(target_distance, digits)

        result = {
            "status": "ok",
            "mode": "read_only_precision_check",
            "broker": "ctrader",
            "account_id": payload.get("account_id"),
            "symbol": payload.get("broker_symbol") or payload.get("symbol"),
            "symbol_digits_used": digits,
            "relative_unit_quantum": quantum,
            "decision": signal.get("decision", "NO_TRADE"),
            "final_decision": payload.get("final_decision", "NO_TRADE"),
            "reference_price": price,
            "stop_distance": round(stop_distance, 8),
            "target_distance": round(target_distance, 8),
            "relative_stop_raw": stop_raw,
            "relative_stop_aligned": stop_aligned,
            "relative_target_raw": target_raw,
            "relative_target_aligned": target_aligned,
            "stop_changed_by_alignment": stop_raw != stop_aligned,
            "target_changed_by_alignment": target_raw != target_aligned,
            "orders_enabled": False,
            "execution_attempted": False,
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "mode": "read_only_precision_check",
            "error": f"{type(exc).__name__}: {exc}",
            "orders_enabled": False,
            "execution_attempted": False,
        }, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

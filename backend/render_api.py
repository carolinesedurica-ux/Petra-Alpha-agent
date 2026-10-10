"""Render cTrader bridge for Petra.

Exposes account telemetry, autonomous shadow analysis and a separately gated
DEMO-only autonomous execution cycle through the private Render bridge.
Funded/live cTrader execution remains impossible from this service.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Petra cTrader Bridge")
ROOT = Path(__file__).resolve().parent
_last_demo_cycle: dict = {"status": "not_run"}
_demo_task = None


def _as_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _bridge_authorized(authorization: str | None) -> bool:
    token = (os.environ.get("PETRA_RENDER_BRIDGE_TOKEN") or "").strip()
    if not token or not authorization:
        return False
    return hmac.compare_digest(authorization, f"Bearer {token}")


def _base_gate() -> tuple[bool, dict]:
    provider = (os.environ.get("BROKER_PROVIDER") or "").strip().lower()
    environment = (os.environ.get("PETRA_CTRADER_ENV") or "demo").strip().lower()
    dry_run = _as_bool("PETRA_CTRADER_DRY_RUN", True)
    armed = _as_bool("PETRA_CTRADER_PILOT_ARMED", False)
    allow_live = _as_bool("ALLOW_LIVE_TRADING", False)
    expected = (os.environ.get("CTRADER_EXPECTED_ACCOUNT_ID") or "").strip()
    creds = all(bool((os.environ.get(k) or "").strip()) for k in (
        "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN"
    ))
    ready = (
        provider == "ctrader"
        and environment == "demo"
        and dry_run
        and not armed
        and not allow_live
        and bool(expected)
        and creds
    )
    return ready, {
        "service": "Petra Alpha Agent",
        "broker_provider": provider,
        "ctrader_environment": environment,
        "expected_account_id": expected or None,
        "dry_run": dry_run,
        "pilot_armed": armed,
        "allow_live_trading": allow_live,
        "ready_for_read_only": ready,
        "execution_enabled": False,
    }


def _demo_execution_gate() -> tuple[bool, dict]:
    base_ready, status = _base_gate()
    enabled = _as_bool("PETRA_CTRADER_DEMO_EXECUTION_ENABLED", False)
    confirm = (os.environ.get("PETRA_CTRADER_DEMO_EXECUTION_CONFIRM") or "").strip()
    interval = max(300, int(os.environ.get("PETRA_CTRADER_DEMO_INTERVAL_SECONDS") or "300"))
    ready = base_ready and enabled and confirm == "CTRADER_DEMO_AUTONOMOUS"
    status.update({
        "demo_execution_enabled": enabled,
        "demo_execution_confirmed": confirm == "CTRADER_DEMO_AUTONOMOUS",
        "demo_autonomous_ready": ready,
        "demo_interval_seconds": interval,
        "funded_execution_enabled": False,
    })
    return ready, status


def _run_json_script(script_name: str, timeout: int) -> tuple[int, dict]:
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / script_name)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 504, {
            "broker": "ctrader",
            "status": "error",
            "execution_enabled": False,
            "error": f"{script_name} timed out on Render",
        }

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return 502, {
            "broker": "ctrader",
            "status": "error",
            "execution_enabled": False,
            "error": f"{script_name} returned no data on Render",
        }

    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return 502, {
            "broker": "ctrader",
            "status": "error",
            "execution_enabled": False,
            "error": f"{script_name} returned invalid data on Render",
        }

    ok = completed.returncode == 0 and payload.get("status") == "ok"
    payload["source"] = "render"
    return (200 if ok else 502), payload


async def _run_demo_cycle_once() -> tuple[int, dict]:
    global _last_demo_cycle
    ready, status = _demo_execution_gate()
    if not ready:
        payload = {
            "broker": "ctrader",
            "mode": "autonomous_demo_execution",
            "status": "blocked",
            "execution_enabled": False,
            "funded_execution_enabled": False,
            "error": "Dedicated cTrader DEMO autonomous execution gate is not satisfied",
            **status,
        }
        _last_demo_cycle = payload
        return 503, payload

    status_code, payload = await asyncio.to_thread(
        _run_json_script, "ctrader_demo_executor.py", 80
    )
    payload["funded_execution_enabled"] = False
    payload["execution_scope"] = "ctrader_demo_only"
    _last_demo_cycle = payload
    return status_code, payload


async def _autonomous_demo_loop() -> None:
    # Small delay lets the startup smoke test/server settle before the first cycle.
    await asyncio.sleep(20)
    while True:
        ready, status = _demo_execution_gate()
        interval = int(status.get("demo_interval_seconds") or 300)
        if ready:
            try:
                code, payload = await _run_demo_cycle_once()
                print(
                    "PETRA_DEMO_CYCLE "
                    + json.dumps({
                        "http": code,
                        "status": payload.get("status"),
                        "action": payload.get("action"),
                        "decision": payload.get("decision"),
                        "final_decision": payload.get("final_decision"),
                        "error": payload.get("error"),
                    }, sort_keys=True),
                    flush=True,
                )
            except Exception as exc:
                print(f"PETRA_DEMO_CYCLE_ERROR {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def start_demo_worker() -> None:
    global _demo_task
    if _demo_task is None:
        _demo_task = asyncio.create_task(_autonomous_demo_loop())


@app.get("/")
async def root():
    ready, _ = _base_gate()
    demo_ready, _ = _demo_execution_gate()
    return {
        "service": "Petra cTrader bridge",
        "status": "online",
        "read_only_ready": ready,
        "demo_autonomous_ready": demo_ready,
        "funded_execution_enabled": False,
    }


@app.get("/health")
async def health():
    ready, status = _demo_execution_gate()
    base_ready = bool(status.get("ready_for_read_only"))
    return {
        "status": "ok" if base_ready else "blocked",
        **status,
        "demo_autonomous_ready": ready,
        "last_demo_cycle": _last_demo_cycle,
    }


@app.get("/api/ctrader/status")
async def ctrader_status(authorization: str | None = Header(default=None)):
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")
    _, status = _demo_execution_gate()
    status["last_demo_cycle"] = _last_demo_cycle
    return JSONResponse(status_code=200, headers={"Cache-Control": "no-store"}, content=status)


@app.get("/api/ctrader/snapshot")
async def ctrader_snapshot(authorization: str | None = Header(default=None)):
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")

    ready, _ = _base_gate()
    if not ready:
        return JSONResponse(
            status_code=503,
            headers={"Cache-Control": "no-store"},
            content={
                "service": "Petra Alpha Agent",
                "broker": "ctrader",
                "mode": "read_only",
                "status": "blocked",
                "execution_enabled": False,
                "error": "cTrader Render safety gate is not satisfied",
            },
        )

    status_code, payload = _run_json_script("ctrader_snapshot.py", 40)
    payload["execution_enabled"] = False
    return JSONResponse(status_code=status_code, headers={"Cache-Control": "no-store"}, content=payload)


@app.get("/api/ctrader/analysis")
async def ctrader_analysis(authorization: str | None = Header(default=None)):
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")

    ready, _ = _base_gate()
    if not ready:
        return JSONResponse(
            status_code=503,
            headers={"Cache-Control": "no-store"},
            content={
                "service": "Petra Alpha Agent",
                "broker": "ctrader",
                "mode": "autonomous_shadow_analysis",
                "status": "blocked",
                "execution_enabled": False,
                "error": "cTrader autonomous analysis requires demo dry-run safety gates",
            },
        )

    status_code, payload = _run_json_script("ctrader_autonomous_bridge.py", 55)
    payload["execution_enabled"] = False
    payload["orders_enabled"] = False
    return JSONResponse(status_code=status_code, headers={"Cache-Control": "no-store"}, content=payload)


@app.post("/api/ctrader/demo-cycle")
async def ctrader_demo_cycle(authorization: str | None = Header(default=None)):
    """Run exactly one guarded autonomous DEMO execution cycle."""
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")
    status_code, payload = await _run_demo_cycle_once()
    return JSONResponse(status_code=status_code, headers={"Cache-Control": "no-store"}, content=payload)

"""Render-only cTrader bridge for Petra.

Exposes safe cTrader account telemetry and autonomous shadow analysis to Petra's
Vercel frontend through a private server-to-server bridge token. No order
submission endpoints exist here.
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Petra cTrader Read-Only Bridge")
ROOT = Path(__file__).resolve().parent


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


def _safe_gate() -> tuple[bool, dict]:
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
    payload["execution_enabled"] = False
    return (200 if ok else 502), payload


@app.get("/")
async def root():
    ready, _ = _safe_gate()
    return {"service": "Petra cTrader bridge", "status": "online", "read_only_ready": ready}


@app.get("/health")
async def health():
    ready, status = _safe_gate()
    return {"status": "ok" if ready else "blocked", **status}


@app.get("/api/ctrader/status")
async def ctrader_status(authorization: str | None = Header(default=None)):
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")
    _, status = _safe_gate()
    return JSONResponse(status_code=200, headers={"Cache-Control": "no-store"}, content=status)


@app.get("/api/ctrader/snapshot")
async def ctrader_snapshot(authorization: str | None = Header(default=None)):
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")

    ready, _ = _safe_gate()
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
    return JSONResponse(status_code=status_code, headers={"Cache-Control": "no-store"}, content=payload)


@app.get("/api/ctrader/analysis")
async def ctrader_analysis(authorization: str | None = Header(default=None)):
    """Run Petra's real-market US500 autonomous analysis in shadow mode only."""
    if not _bridge_authorized(authorization):
        raise HTTPException(status_code=401, detail="bridge authentication required")

    ready, _ = _safe_gate()
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

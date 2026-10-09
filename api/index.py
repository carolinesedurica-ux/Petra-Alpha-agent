import sys
import os
import json
import hmac
import subprocess
import traceback
import httpx
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.requests import Request

# Make backend modules importable from the Vercel serverless function context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from server import app  # noqa: F401


def _as_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _operator_authorized(request: Request) -> bool:
    token = (os.environ.get("PETRA_OPERATOR_TOKEN") or "").strip()
    if not token:
        return False
    supplied = request.headers.get("authorization", "")
    return hmac.compare_digest(supplied, f"Bearer {token}")


@app.middleware("http")
async def ctrader_runtime_status_middleware(request: Request, call_next):
    """Expose safe cTrader runtime checks and an authenticated read-only snapshot."""
    if request.method == "GET" and request.url.path == "/api/ctrader/status":
        provider = (os.environ.get("BROKER_PROVIDER") or "alpaca").strip().lower()
        environment = (os.environ.get("PETRA_CTRADER_ENV") or "demo").strip().lower()
        expected_account_id = (os.environ.get("CTRADER_EXPECTED_ACCOUNT_ID") or "").strip()
        has_client_id = bool((os.environ.get("CTRADER_CLIENT_ID") or "").strip())
        has_client_secret = bool((os.environ.get("CTRADER_CLIENT_SECRET") or "").strip())
        has_access_token = bool((os.environ.get("CTRADER_ACCESS_TOKEN") or "").strip())
        dry_run = _as_bool("PETRA_CTRADER_DRY_RUN", True)
        armed = _as_bool("PETRA_CTRADER_PILOT_ARMED", False)
        generic_live = _as_bool("ALLOW_LIVE_TRADING", False)

        ready_for_read_only = (
            provider == "ctrader"
            and environment == "demo"
            and bool(expected_account_id)
            and has_client_id
            and has_client_secret
            and has_access_token
            and dry_run
            and not armed
            and not generic_live
        )

        return JSONResponse(
            status_code=200,
            headers={"Cache-Control": "no-store, max-age=0"},
            content={
                "service": "Petra Alpha Agent",
                "broker_provider": provider,
                "ctrader_environment": environment,
                "expected_account_id": expected_account_id or None,
                "credentials_present": {
                    "client_id": has_client_id,
                    "client_secret": has_client_secret,
                    "access_token": has_access_token,
                },
                "dry_run": dry_run,
                "pilot_armed": armed,
                "allow_live_trading": generic_live,
                "ready_for_read_only": ready_for_read_only,
                "execution_enabled": False,
            },
        )

    if request.method == "GET" and request.url.path == "/api/ctrader/snapshot":
        if not _operator_authorized(request):
            return JSONResponse(
                status_code=401,
                headers={"Cache-Control": "no-store, max-age=0"},
                content={"detail": "operator authentication required"},
            )

        provider = (os.environ.get("BROKER_PROVIDER") or "alpaca").strip().lower()
        environment = (os.environ.get("PETRA_CTRADER_ENV") or "demo").strip().lower()
        dry_run = _as_bool("PETRA_CTRADER_DRY_RUN", True)
        armed = _as_bool("PETRA_CTRADER_PILOT_ARMED", False)
        generic_live = _as_bool("ALLOW_LIVE_TRADING", False)

        if not (
            provider == "ctrader"
            and environment == "demo"
            and dry_run
            and not armed
            and not generic_live
        ):
            return JSONResponse(
                status_code=503,
                headers={"Cache-Control": "no-store, max-age=0"},
                content={
                    "service": "Petra Alpha Agent",
                    "broker": "ctrader",
                    "mode": "read_only",
                    "status": "blocked",
                    "execution_enabled": False,
                    "error": "cTrader snapshot safety gate is not satisfied",
                },
            )

        render_url = (os.environ.get("PETRA_CTRADER_RENDER_URL") or "").strip().rstrip("/")
        bridge_token = (os.environ.get("PETRA_RENDER_BRIDGE_TOKEN") or "").strip()
        if not render_url or not bridge_token:
            return JSONResponse(
                status_code=503,
                headers={"Cache-Control": "no-store, max-age=0"},
                content={
                    "service": "Petra Alpha Agent",
                    "broker": "ctrader",
                    "mode": "read_only",
                    "status": "blocked",
                    "execution_enabled": False,
                    "error": "Render cTrader bridge is not configured",
                },
            )

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.get(
                    f"{render_url}/api/ctrader/snapshot",
                    headers={"Authorization": f"Bearer {bridge_token}"},
                )
        except httpx.HTTPError as exc:
            return JSONResponse(
                status_code=502,
                headers={"Cache-Control": "no-store, max-age=0"},
                content={
                    "service": "Petra Alpha Agent",
                    "broker": "ctrader",
                    "mode": "read_only",
                    "status": "error",
                    "execution_enabled": False,
                    "error": f"Render bridge request failed: {type(exc).__name__}",
                },
            )

        try:
            payload = response.json()
        except ValueError:
            payload = {
                "service": "Petra Alpha Agent",
                "broker": "ctrader",
                "mode": "read_only",
                "status": "error",
                "execution_enabled": False,
                "error": "Render bridge returned invalid data",
            }

        payload["proxied_via"] = "vercel"
        payload["execution_enabled"] = False
        return JSONResponse(
            status_code=response.status_code,
            headers={"Cache-Control": "no-store, max-age=0"},
            content=payload,
        )

    return await call_next(request)


@app.middleware("http")
async def ctrader_oauth_callback_middleware(request: Request, call_next):
    """Public OAuth landing endpoint for cTrader.

    This intentionally does not log or expose the authorization code. Token exchange
    will be enabled only after cTrader credentials are stored in protected deployment
    secrets and state validation is wired in.
    """
    if request.method == "GET" and request.url.path == "/api/ctrader/callback":
        error = request.query_params.get("error")
        code = request.query_params.get("code")

        headers = {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        }

        if error:
            return HTMLResponse(
                status_code=400,
                headers=headers,
                content=(
                    "<!doctype html><html><head><title>Petra cTrader authorization</title></head>"
                    "<body><h1>Authorization was not completed</h1>"
                    "<p>Return to Petra and try the cTrader authorization again.</p></body></html>"
                ),
            )

        if code:
            return HTMLResponse(
                status_code=200,
                headers=headers,
                content=(
                    "<!doctype html><html><head><title>Petra cTrader authorization</title></head>"
                    "<body><h1>cTrader authorization received</h1>"
                    "<p>Petra received the authorization response. No trading action was taken.</p>"
                    "<p>You may close this window.</p></body></html>"
                ),
            )

        return JSONResponse(
            status_code=200,
            headers=headers,
            content={
                "service": "Petra Alpha Agent",
                "endpoint": "cTrader OAuth callback",
                "status": "ready",
                "trading_enabled": False,
            },
        )

    return await call_next(request)


@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        err_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(f"VERCEL_ERROR [{request.url.path}]:\n{err_str}")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "path": request.url.path}
        )

import sys
import os
import traceback
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.requests import Request

# Make backend modules importable from the Vercel serverless function context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from server import app  # noqa: F401


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

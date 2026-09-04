import sys
import os
import traceback
from fastapi.responses import JSONResponse
from starlette.requests import Request

# Make backend modules importable from the Vercel serverless function context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from server import app  # noqa: F401

@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        err_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        print(f"VERCEL_ERROR [{request.url.path}]:\n{err_str}")
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "path": request.url.path, "traceback": err_str}
        )

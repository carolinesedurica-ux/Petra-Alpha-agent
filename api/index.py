import os
import sys
import traceback

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from server import app as fastapi_app
    import_error = None
except Exception:
    fastapi_app = None
    import_error = traceback.format_exc()

async def app(scope, receive, send):
    if import_error:
        response_body = f"STARTUP_IMPORT_ERROR:\n{import_error}".encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [
                [b"content-type", b"text/plain; charset=utf-8"],
                [b"content-length", str(len(response_body)).encode("utf-8")],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": response_body,
        })
        return

    try:
        await fastapi_app(scope, receive, send)
    except Exception:
        err = traceback.format_exc()
        response_body = f"ASGI_RUNTIME_ERROR:\n{err}".encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [
                [b"content-type", b"text/plain; charset=utf-8"],
                [b"content-length", str(len(response_body)).encode("utf-8")],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": response_body,
        })

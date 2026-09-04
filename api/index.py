import os
import sys
import traceback
import logging

curr_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(curr_dir, ".."))

paths_to_add = [
    os.path.join(root_dir, "backend"),
    os.path.join(curr_dir, "backend"),
    os.path.join(os.getcwd(), "backend"),
    root_dir,
    curr_dir,
]
for p in paths_to_add:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    from server import app
except Exception as e:
    err_tb = traceback.format_exc()
    logging.error("Failed to import server app in api/index.py: %s\n%s", e, err_tb)
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Petra Alpha Agent - Error Fallback")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def catch_all_error(path: str):
        return JSONResponse(
            status_code=500,
            content={
                "error": "Backend server failed to import",
                "detail": str(e),
                "traceback": err_tb,
                "sys_path": sys.path,
                "cwd": os.getcwd(),
                "cwd_files": os.listdir(".") if os.path.exists(".") else [],
            }
        )

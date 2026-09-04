import sys
import os
import traceback
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="Petra Debug Endpoint")

@app.get("/api/debug")
@app.get("/debug")
async def debug_endpoint():
    res = {
        "status": "ok",
        "python": sys.version,
        "cwd": os.getcwd(),
        "files_root": os.listdir(".") if os.path.exists(".") else [],
        "sys_path": sys.path,
        "env_has_alpaca_key": bool(os.environ.get("ALPACA_API_KEY") or os.environ.get("APCA_API_KEY_ID")),
        "env_has_alpaca_secret": bool(os.environ.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_API_SECRET") or os.environ.get("APCA_API_SECRET_KEY")),
        "env_has_featherless_key": bool(os.environ.get("FEATHERLESS_API_KEY")),
        "env_keys": [k for k in os.environ.keys() if "KEY" not in k and "SECRET" not in k],
    }

    # Check packages
    pkg_status = {}
    for pkg in ["fastapi", "starlette", "motor", "pymongo", "mongomock", "mongomock_motor", "httpx", "pydantic", "openai"]:
        try:
            mod = __import__(pkg)
            pkg_status[pkg] = "OK"
        except Exception as e:
            pkg_status[pkg] = f"FAILED: {e}"
    res["packages"] = pkg_status

    # Check backend directory
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(curr_dir, ".."))
    res["paths_checked"] = {
        "curr_dir": curr_dir,
        "root_dir": root_dir,
        "api_exists": os.path.exists(os.path.join(root_dir, "api")),
        "backend_exists": os.path.exists(os.path.join(root_dir, "backend")),
        "backend_files": os.listdir(os.path.join(root_dir, "backend")) if os.path.exists(os.path.join(root_dir, "backend")) else [],
    }

    # Check importing backend.server
    try:
        for p in [os.path.join(root_dir, "backend"), os.path.join(curr_dir, "backend"), "backend", root_dir]:
            if os.path.exists(p) and p not in sys.path:
                sys.path.insert(0, p)
        import server
        res["import_server"] = "OK"
    except Exception as e:
        res["import_server"] = f"FAILED: {traceback.format_exc()}"

    return JSONResponse(content=res)

import sys
import os
import traceback
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        
        info = []
        info.append(f"Python Version: {sys.version}")
        info.append(f"Current Directory: {os.getcwd()}")
        try:
            info.append(f"Files in Current Directory: {os.listdir('.')}")
        except Exception as e:
            info.append(f"Cannot list current dir: {e}")
            
        if os.path.exists("api"):
            info.append(f"Files in api/: {os.listdir('api')}")
        else:
            info.append("api/ DOES NOT EXIST")
            
        if os.path.exists("backend"):
            info.append(f"Files in backend/: {os.listdir('backend')}")
        else:
            info.append("backend/ DOES NOT EXIST")
            
        # Try importing dependencies
        for pkg in ["fastapi", "starlette", "motor", "pymongo", "mongomock", "mongomock_motor", "httpx", "pydantic", "openai"]:
            try:
                __import__(pkg)
                info.append(f"Package {pkg}: INSTALLED")
            except Exception as e:
                info.append(f"Package {pkg}: MISSING ({e})")
                
        # Try importing backend.server
        try:
            backend_path = os.path.abspath("backend")
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)
            import server
            info.append("SUCCESS: imported server.py!")
        except Exception as e:
            info.append(f"FAILED importing server.py:\n{traceback.format_exc()}")
            
        output = "\n----------------------------------------\n".join(info)
        self.wfile.write(output.encode('utf-8'))
        return

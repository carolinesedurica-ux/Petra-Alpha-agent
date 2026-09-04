import os
import sys

# Ensure root and backend directories are in sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
backend_dir = os.path.join(root_dir, "backend")
for p in (backend_dir, root_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

from server import app

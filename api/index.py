import sys
import os

# Make backend modules importable from the Vercel serverless function context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from server import app  # noqa: F401 – Vercel picks up the FastAPI app via this import

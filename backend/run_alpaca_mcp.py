"""Runner for the Official Alpaca MCP Server (alpaca-mcp-server).

Loads verified paper credentials from backend/.env and launches the
official Alpaca FastMCP server with stdio or SSE transport.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

# Ensure required environment variables for alpaca-mcp-server
os.environ["ALPACA_API_KEY"] = os.environ.get("ALPACA_API_KEY", "")
os.environ["ALPACA_SECRET_KEY"] = os.environ.get("ALPACA_API_SECRET", os.environ.get("ALPACA_SECRET_KEY", ""))
os.environ["ALPACA_PAPER_TRADE"] = "true"

print(f"[Alpaca MCP Runner] Authenticating with Key: {os.environ['ALPACA_API_KEY'][:6]}... Paper Mode: True")

if __name__ == "__main__":
    from alpaca_mcp_server.server import build_server
    server = build_server()
    server.run()

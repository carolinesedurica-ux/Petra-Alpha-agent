"""Test live trade data feeds from Alpaca."""
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_KEY = os.environ.get("ALPACA_API_KEY")
API_SECRET = os.environ.get("ALPACA_API_SECRET")
DATA_URL = os.environ.get("ALPACA_DATA_URL", "https://data.alpaca.markets")
TRADING_URL = os.environ.get("ALPACA_TRADING_URL", "https://paper-api.alpaca.markets/v2")

headers = {
    "APCA-API-KEY-ID": API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET
}

print("=== 1. LIVE UNDERLYING TICKER TRADES (IEX Feed) ===")
r = httpx.get(f"{DATA_URL}/v2/stocks/snapshots?symbols=SPY,QQQ,NVDA&feed=iex", headers=headers, timeout=15)
for sym, d in r.json().items():
    trade = d.get("latestTrade") or {}
    quote = d.get("latestQuote") or {}
    bar = d.get("dailyBar") or {}
    print(f"[{sym}] Last Trade: ${trade.get('p', 'N/A')} (Size: {trade.get('s', 'N/A')}) | Bid: ${quote.get('bp', 'N/A')} / Ask: ${quote.get('ap', 'N/A')} | Volume: {bar.get('v', 'N/A'):,}")

print("\n=== 2. LIVE OPTIONS CHAIN & QUOTES (Indicative Feed) ===")
# Fetch first available option contract
cr = httpx.get(f"{TRADING_URL}/options/contracts?underlying_symbols=SPY&status=active&limit=2", headers=headers, timeout=15)
contracts = cr.json().get("option_contracts", [])
if contracts:
    c_sym = contracts[0]["symbol"]
    sr = httpx.get(f"{DATA_URL}/v1beta1/options/snapshots/SPY?feed=indicative", headers=headers, timeout=15)
    snap = sr.json().get("snapshots", {}).get(c_sym, {})
    l_quote = snap.get("latestQuote", {})
    l_trade = snap.get("latestTrade", {})
    greeks = snap.get("greeks", {})
    print(f"Option: {c_sym}")
    print(f"  Bid: ${l_quote.get('bp', 'N/A')} x {l_quote.get('bs', 'N/A')} | Ask: ${l_quote.get('ap', 'N/A')} x {l_quote.get('as', 'N/A')}")
    print(f"  Last Trade: ${l_trade.get('p', 'N/A')} (Size: {l_trade.get('s', 'N/A')}, Time: {l_trade.get('t', 'N/A')})")
    print(f"  Delta: {greeks.get('delta', 'N/A')} | Gamma: {greeks.get('gamma', 'N/A')} | Theta: {greeks.get('theta', 'N/A')} | IV: {snap.get('impliedVolatility', 'N/A')}")

print("\n=== 3. ACCOUNT ORDER BLOTTER & EXECUTIONS ===")
or_r = httpx.get(f"{TRADING_URL}/orders?status=all&limit=3", headers=headers, timeout=15)
orders = or_r.json()
print(f"Total recent orders returned: {len(orders)}")
for o in orders:
    print(f"  Order {o.get('id')[:8]}: {o.get('symbol')} {o.get('side')} {o.get('qty')} @ {o.get('filled_avg_price') or 'unfilled'} ({o.get('status')})")

"""
Alpaca execution layer.

MOCK MODE (default): fully simulated $100k paper account, market data, options
chain and multi-leg fills so the whole agent + dashboard runs end-to-end.

REAL MODE (ALPACA_MODE=cli): wraps the Alpaca CLI for `order_class: mleg` orders.
Kept as a thin, ready-to-wire abstraction — supply ALPACA_API_KEY/SECRET and set
ALPACA_MODE=cli to route real orders. Not exercised until keys are provided.
"""
import os
import json
import random
import math
import subprocess
from datetime import datetime, timezone, timedelta

from pricing import bs_price, bs_delta
from models import now_iso, new_id

MODE = os.environ.get("ALPACA_MODE", "mock")

# Liquid, tight-spread underlyings. (base price, annualized IV, strike spacing)
UNIVERSE = {
    "SPY":  {"px": 545.0, "iv": 0.15, "spacing": 1.0},
    "QQQ":  {"px": 470.0, "iv": 0.20, "spacing": 1.0},
    "IWM":  {"px": 220.0, "iv": 0.22, "spacing": 1.0},
    "AAPL": {"px": 225.0, "iv": 0.26, "spacing": 2.5},
    "MSFT": {"px": 430.0, "iv": 0.24, "spacing": 2.5},
    "NVDA": {"px": 130.0, "iv": 0.48, "spacing": 2.5},
    "TSLA": {"px": 250.0, "iv": 0.55, "spacing": 2.5},
    "META": {"px": 570.0, "iv": 0.30, "spacing": 5.0},
}

INITIAL_EQUITY = 100000.0


def occ_symbol(underlying, expiry_ts, opt_type, strike):
    d = datetime.fromisoformat(expiry_ts).strftime("%y%m%d")
    cp = "C" if opt_type == "call" else "P"
    return f"{underlying}{d}{cp}{int(round(strike * 1000)):08d}"


class MockAlpaca:
    def __init__(self, db):
        self.db = db

    # ---------- account & market state ----------
    async def ensure_seed(self):
        acc = await self.db.account.find_one({"id": "account"})
        if not acc:
            await self.db.account.insert_one({
                "id": "account",
                "equity": INITIAL_EQUITY,
                "cash": INITIAL_EQUITY,
                "buying_power": INITIAL_EQUITY,
                "initial_equity": INITIAL_EQUITY,
                "day_start_equity": INITIAL_EQUITY,
                "updated_at": now_iso(),
            })
        mkt = await self.db.market.find_one({"id": "market"})
        if not mkt:
            syms = {}
            for s, cfg in UNIVERSE.items():
                trend = random.uniform(-0.4, 0.5)
                syms[s] = {
                    "price": cfg["px"],
                    "prev_price": cfg["px"],
                    "iv": cfg["iv"],
                    "spacing": cfg["spacing"],
                    "trend": trend,       # daily drift bias in %
                    "day_open": cfg["px"],
                }
            await self.db.market.insert_one({"id": "market", "symbols": syms, "updated_at": now_iso()})

    async def get_account(self):
        return await self.db.account.find_one({"id": "account"}, {"_id": 0})

    async def get_market(self):
        m = await self.db.market.find_one({"id": "market"}, {"_id": 0})
        return m["symbols"]

    async def advance_market(self):
        """Random-walk each underlying one step, influenced by its trend."""
        m = await self.db.market.find_one({"id": "market"})
        syms = m["symbols"]
        for s, d in syms.items():
            d["prev_price"] = d["price"]
            drift = d["trend"] / 100.0
            shock = random.gauss(0, d["iv"] / math.sqrt(252))
            d["price"] = round(max(1.0, d["price"] * (1 + drift * 0.15 + shock)), 2)
            # keep intraday change realistic
            if abs(d["price"] / d["day_open"] - 1) > 0.03:
                d["day_open"] = round(d["price"] * random.uniform(0.99, 1.01), 2)
            # occasional trend regime flip
            if random.random() < 0.08:
                d["trend"] = random.uniform(-0.6, 0.7)
        await self.db.market.update_one({"id": "market"}, {"$set": {"symbols": syms, "updated_at": now_iso()}})
        return syms

    async def apply_equity_delta(self, cash_delta):
        acc = await self.db.account.find_one({"id": "account"})
        cash = acc["cash"] + cash_delta
        await self.db.account.update_one({"id": "account"}, {"$set": {"cash": cash, "updated_at": now_iso()}})

    async def recompute_equity(self, open_positions):
        """equity = cash + net liquidation value of open credit spreads."""
        acc = await self.db.account.find_one({"id": "account"})
        cash = acc["cash"]
        # value of open spreads = credit received - current cost to close, marked continuously
        open_val = 0.0
        risk_used = 0.0
        for p in open_positions:
            open_val += p["unrealized_pnl"]
            risk_used += p["max_risk"]
        equity = round(cash + open_val, 2)
        buying_power = round(equity - risk_used, 2)
        await self.db.account.update_one(
            {"id": "account"},
            {"$set": {"equity": equity, "buying_power": buying_power, "updated_at": now_iso()}},
        )
        return equity, buying_power

    # ---------- options chain ----------
    def build_chain_leg(self, underlying, S, iv, opt_type, strike, T):
        is_call = opt_type == "call"
        mid = bs_price(S, strike, T, iv, is_call)
        delta = bs_delta(S, strike, T, iv, is_call)
        spread = max(0.02, mid * 0.06)  # simulated bid/ask spread
        bid = max(0.01, mid - spread / 2)
        ask = mid + spread / 2
        oi = int(max(50, 5000 * math.exp(-abs(delta) * 3) + random.randint(-200, 800)))
        return {
            "strike": strike,
            "mid": round(mid, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "delta": round(delta, 4),
            "bid_ask_pct": round(spread / mid, 4) if mid > 0 else 1.0,
            "open_interest": oi,
        }

    def find_strike_by_delta(self, underlying, S, iv, spacing, opt_type, target_delta, T):
        """Walk OTM strikes to find the one closest to target |delta|."""
        base = round(S / spacing) * spacing
        best = None
        step = spacing if opt_type == "call" else -spacing
        k = base
        for _ in range(40):
            leg = self.build_chain_leg(underlying, S, iv, opt_type, k, T)
            if best is None or abs(abs(leg["delta"]) - target_delta) < abs(abs(best["delta"]) - target_delta):
                best = leg
            k += step
            if abs(leg["delta"]) < target_delta * 0.4:
                break
        return best

    # ---------- order placement ----------
    async def place_mleg(self, position_payload, dry_run=False):
        """
        Route an mleg (multi-leg) order. Returns {order_id, status, filled_credit}.
        MOCK: instant fill at mid. CLI: shells out to the Alpaca CLI.
        """
        if MODE == "cli" and os.environ.get("ALPACA_API_KEY"):
            return self._place_via_cli(position_payload, dry_run)
        order_id = f"mock-{new_id()[:8]}"
        return {"order_id": order_id, "status": "filled", "filled_credit": position_payload["credit"], "dry_run": dry_run}

    def _place_via_cli(self, payload, dry_run):
        legs = []
        for leg in payload["legs"]:
            legs.append({
                "symbol": leg["symbol"],
                "side": leg["side"],
                "ratio_qty": "1",
                "position_intent": "sell_to_open" if leg["side"] == "sell" else "buy_to_open",
            })
        cmd = ["alpaca", "orders", "create", "--order-class", "mleg",
               "--qty", str(payload["contracts"]), "--type", "limit",
               "--limit-price", str(payload["credit"]), "--time-in-force", "day",
               "--legs", json.dumps(legs), "--output", "json"]
        if dry_run:
            cmd.append("--dry-run")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(out.stdout or "{}")
            return {"order_id": data.get("id", "cli-unknown"), "status": data.get("status", "accepted"),
                    "filled_credit": payload["credit"], "dry_run": dry_run}
        except Exception as e:  # noqa
            return {"order_id": "", "status": "error", "error": str(e), "filled_credit": 0}

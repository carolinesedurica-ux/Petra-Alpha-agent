"""
Alpaca execution layer.

MOCK MODE (ALPACA_MODE=mock): fully simulated $100k paper account, market data, options
chain and multi-leg fills so the whole agent + dashboard runs end-to-end.

LIVE MODE (ALPACA_MODE=live): Alpaca paper account via REST — real account, IEX stock
snapshots, real options chain (indicative feed, greeks), and `order_class: mleg` orders.

Both classes expose the same interface consumed by agent.py / engines.py / server.py.
"""
import os
import asyncio
import random
import math
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo

import httpx

from pricing import bs_price, bs_delta
from models import now_iso, new_id, Decision

MODE = os.environ.get("ALPACA_MODE", "mock")
ET = ZoneInfo("America/New_York")

# Liquid, tight-spread underlyings. (base price, annualized IV, strike spacing) — mock defaults
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


def _remaining_days(expiry_ts):
    return max(0.0, (datetime.fromisoformat(expiry_ts) - datetime.now(timezone.utc)).total_seconds() / 86400.0)


def _bs_close_value(p, market):
    """Net debit (per share) to buy the spread back, marked to Black-Scholes."""
    S = market[p["underlying"]]["price"]
    iv = market[p["underlying"]]["iv"]
    remain = _remaining_days(p["expiry_ts"])
    T = max(remain, 0.02) / 365.0
    val = 0.0
    for leg in p["legs"]:
        price = bs_price(S, leg["strike"], T, iv, leg["option_type"] == "call")
        val += price if leg["side"] == "sell" else -price
    return round(max(0.0, val), 2), remain


async def record_snapshot(db, equity, open_positions):
    m = await db.market.find_one({"id": "market"}, {"_id": 0}) or {"symbols": {}}
    await db.pnl_snapshots.insert_one({
        "id": new_id(), "ts": now_iso(), "equity": round(equity, 2),
        "spy": m["symbols"].get("SPY", {}).get("price"),
        "open_risk": round(sum(p["max_risk"] for p in open_positions), 2),
        "open_positions": len(open_positions)})


async def log_order(db, meta, payload, result):
    await db.orders.insert_one({
        "id": new_id(), "ts": now_iso(), **meta,
        "order_type": payload.get("type"), "limit_price": payload.get("limit_price"),
        "qty": int(payload["qty"]), "legs": payload["legs"],
        "alpaca_order_id": result.get("order_id", ""), "client_order_id": payload.get("client_order_id", ""),
        "status": result.get("alpaca_status", result["status"]),
        "filled_price": result.get("filled_credit", result.get("filled_debit", 0.0)) or 0.0})


def _open_payload(proposal, cycle_id):
    legs = [{"symbol": l["symbol"], "ratio_qty": "1", "side": l["side"],
             "position_intent": "sell_to_open" if l["side"] == "sell" else "buy_to_open"}
            for l in proposal["legs"]]
    return {"order_class": "mleg", "type": "limit", "qty": str(proposal["contracts"]),
            "limit_price": str(_tick(-proposal["credit"])), "time_in_force": "day",
            "client_order_id": f"petra-{cycle_id}-{new_id()[:8]}", "legs": legs}


def _close_payload(position, urgent):
    legs = [{"symbol": l["symbol"], "ratio_qty": "1", "side": "buy" if l["side"] == "sell" else "sell",
             "position_intent": "buy_to_close" if l["side"] == "sell" else "sell_to_close"}
            for l in position["legs"]]
    payload = {"order_class": "mleg", "qty": str(position["contracts"]), "time_in_force": "day",
               "client_order_id": f"petra-close-{new_id()[:8]}", "legs": legs}
    if urgent:
        payload["type"] = "market"
    else:
        payload.update(type="limit", limit_price=str(_tick(position["current_value"] + 0.03)))
    return payload


def _tick(px):
    """Option limit price increments: $0.01 under $3, $0.05 above."""
    a = abs(px)
    a = round(a, 2) if a < 3 else round(round(a / 0.05) * 0.05, 2)
    return a if px >= 0 else -a


class MockAlpaca:
    mode = "mock"

    def __init__(self, db):
        self.db = db

    # ---------- account & market state ----------
    async def ensure_seed(self):
        acc = await self.db.account.find_one({"id": "account"})
        if not acc or acc.get("mode") != "mock":
            for c in ("positions", "decisions", "pnl_snapshots", "market", "account"):
                await self.db[c].delete_many({})
            await self.db.account.insert_one({
                "id": "account", "mode": "mock", "account_number": "PA-ALPHA-PAPER-100K",
                "equity": INITIAL_EQUITY, "cash": INITIAL_EQUITY, "buying_power": INITIAL_EQUITY,
                "initial_equity": INITIAL_EQUITY, "day_start_equity": INITIAL_EQUITY,
                "updated_at": now_iso(),
            })
        mkt = await self.db.market.find_one({"id": "market"})
        if not mkt:
            syms = {}
            for s, cfg in UNIVERSE.items():
                syms[s] = {"price": cfg["px"], "prev_price": cfg["px"], "iv": cfg["iv"],
                           "spacing": cfg["spacing"], "trend": random.uniform(-0.4, 0.5), "day_open": cfg["px"]}
            await self.db.market.insert_one({"id": "market", "symbols": syms, "updated_at": now_iso()})

    async def market_open(self):
        from agent import market_status
        return market_status()["open"]

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
            if abs(d["price"] / d["day_open"] - 1) > 0.03:
                d["day_open"] = round(d["price"] * random.uniform(0.99, 1.01), 2)
            if random.random() < 0.08:
                d["trend"] = random.uniform(-0.6, 0.7)
        await self.db.market.update_one({"id": "market"}, {"$set": {"symbols": syms, "updated_at": now_iso()}})
        return syms

    async def apply_equity_delta(self, cash_delta):
        acc = await self.db.account.find_one({"id": "account"})
        await self.db.account.update_one({"id": "account"}, {"$set": {"cash": acc["cash"] + cash_delta, "updated_at": now_iso()}})

    async def recompute_equity(self, open_positions):
        """equity = cash + net liquidation value of open credit spreads."""
        acc = await self.db.account.find_one({"id": "account"})
        open_val = sum(p["unrealized_pnl"] for p in open_positions)
        risk_used = sum(p["max_risk"] for p in open_positions)
        equity = round(acc["cash"] + open_val, 2)
        buying_power = round(equity - risk_used, 2)
        await self.db.account.update_one({"id": "account"}, {"$set": {
            "equity": equity, "buying_power": buying_power, "updated_at": now_iso()}})
        return equity, buying_power

    async def close_values(self, open_positions, market):
        return {p["id"]: _bs_close_value(p, market) for p in open_positions}

    # ---------- options chain ----------
    async def load_chain(self, underlying, S, cfg):
        return None

    def expiry_ts(self, underlying, dte):
        return (datetime.now(timezone.utc) + timedelta(days=dte)).isoformat()

    def build_chain_leg(self, underlying, S, iv, opt_type, strike, T):
        is_call = opt_type == "call"
        mid = bs_price(S, strike, T, iv, is_call)
        delta = bs_delta(S, strike, T, iv, is_call)
        spread = max(0.02, mid * 0.06)
        bid = max(0.01, mid - spread / 2)
        ask = mid + spread / 2
        oi = int(max(50, 5000 * math.exp(-abs(delta) * 3) + random.randint(-200, 800)))
        return {"strike": strike, "mid": round(mid, 2), "bid": round(bid, 2), "ask": round(ask, 2),
                "delta": round(delta, 4), "bid_ask_pct": round(spread / mid, 4) if mid > 0 else 1.0,
                "open_interest": oi, "symbol": None}

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

    # ---------- orders ----------
    async def reconcile(self, open_positions):
        return []

    async def place_mleg(self, proposal, cycle_id=""):
        """Instant fill at mid; credit lands in cash."""
        await self.apply_equity_delta(proposal["credit"] * 100 * proposal["contracts"])
        res = {"order_id": f"mock-{new_id()[:8]}", "status": "filled", "filled_credit": proposal["credit"]}
        await log_order(self.db, {"intent": "open", "underlying": proposal["underlying"], "strategy": proposal["strategy"],
                                  "cycle_id": cycle_id, "mode": "mock"}, _open_payload(proposal, cycle_id), res)
        return res

    async def close_mleg(self, position, urgent=False, reason=""):
        debit = position["current_value"]
        realized = round((position["credit"] - debit) * 100 * position["contracts"], 2)
        await self.apply_equity_delta(realized)
        res = {"order_id": f"mock-{new_id()[:8]}", "status": "filled", "filled_debit": debit}
        await log_order(self.db, {"intent": "close", "underlying": position["underlying"], "strategy": position["strategy"],
                                  "reason": reason, "position_id": position["id"], "mode": "mock"}, _close_payload(position, urgent), res)
        return res


class LiveAlpaca:
    mode = "live"

    def __init__(self, db):
        self.db = db
        self.trading = os.environ["ALPACA_TRADING_URL"]
        self.data = os.environ["ALPACA_DATA_URL"]
        self.http = httpx.AsyncClient(headers={
            "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
            "Accept": "application/json"}, timeout=20)
        self._chains = {}
        self._last_reconcile = None

    async def _req(self, method, base, path, **kwargs):
        r = await self.http.request(method, base + path, **kwargs)
        if r.is_error:
            raise RuntimeError(f"Alpaca {method} {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # ---------- account ----------
    async def _fetch_account(self):
        acc = await self._req("GET", self.trading, "/account")
        upd = {"equity": float(acc["equity"]), "cash": float(acc["cash"]),
               "buying_power": float(acc["options_buying_power"]),
               "day_start_equity": float(acc["last_equity"]),
               "account_number": acc["account_number"], "updated_at": now_iso()}
        await self.db.account.update_one({"id": "account"}, {"$set": upd})
        return upd

    async def ensure_seed(self):
        acc = await self.db.account.find_one({"id": "account"})
        if not acc or acc.get("mode") != "live":
            for c in ("positions", "decisions", "pnl_snapshots", "market", "account"):
                await self.db[c].delete_many({})
            raw = await self._req("GET", self.trading, "/account")
            await self.db.account.insert_one({
                "id": "account", "mode": "live", "account_number": raw["account_number"],
                "initial_equity": float(raw["equity"]), "equity": float(raw["equity"]),
                "cash": float(raw["cash"]), "buying_power": float(raw["options_buying_power"]),
                "day_start_equity": float(raw["last_equity"]), "updated_at": now_iso()})
        await self._fetch_account()
        if not await self.db.market.find_one({"id": "market"}):
            await self.advance_market()
        if await self.db.pnl_snapshots.count_documents({}) == 0:
            acc = await self.get_account()
            await record_snapshot(self.db, acc["equity"], [])

    async def market_open(self):
        clock = await self._req("GET", self.trading, "/clock")
        return bool(clock["is_open"])

    async def get_account(self):
        return await self.db.account.find_one({"id": "account"}, {"_id": 0})

    async def apply_equity_delta(self, cash_delta):
        return None  # Alpaca owns cash accounting in live mode

    async def recompute_equity(self, open_positions):
        acc = await self._fetch_account()
        last = await self.db.pnl_snapshots.find_one({}, sort=[("ts", -1)])
        if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last["ts"])).total_seconds() > 300:
            await record_snapshot(self.db, acc["equity"], open_positions)
        return acc["equity"], acc["buying_power"]

    # ---------- reconciliation ----------
    async def reconcile(self, open_positions, force=False):
        """Compare DB spreads with Alpaca /positions; close stale rows, flag mismatches & orphans."""
        now = datetime.now(timezone.utc)
        if not force and self._last_reconcile and (now - self._last_reconcile).total_seconds() < 60:
            return []
        self._last_reconcile = now
        live = await self._req("GET", self.trading, "/positions")
        held = {x["symbol"]: float(x["qty"]) for x in live}
        events = []
        ours = set()
        for p in open_positions:
            syms = [l["symbol"] for l in p["legs"]]
            ours.update(syms)
            present = [s for s in syms if s in held]
            if len(present) == len(syms):
                continue
            if present:
                if not p.get("reconcile_warned"):
                    await self.db.positions.update_one({"id": p["id"]}, {"$set": {"reconcile_warned": True}})
                    await self.db.decisions.insert_one(Decision(
                        cycle_id="reconcile", underlying=p["underlying"], strategy=p["strategy"], outcome="error",
                        reason=f"RECONCILE: only {len(present)}/{len(syms)} legs found at Alpaca — partial fill/assignment? Review manually.",
                        position_id=p["id"]).model_dump())
                continue
            expired = datetime.fromisoformat(p["expiry_ts"]) < now
            stock_qty = held.get(p["underlying"], 0.0)
            if expired and not stock_qty:
                reason, realized = "expired", round(p["credit"] * 100 * p["contracts"], 2)
            elif stock_qty:
                reason, realized = "assigned", round(p.get("unrealized_pnl", 0.0), 2)
            else:
                reason, realized = "external_close", round(p.get("unrealized_pnl", 0.0), 2)
            await self.db.positions.update_one({"id": p["id"]}, {"$set": {
                "status": "closed", "exit_reason": reason, "realized_pnl": realized, "closed_at": now_iso()}})
            await self.db.decisions.insert_one(Decision(
                cycle_id="reconcile", underlying=p["underlying"], strategy=p["strategy"],
                outcome="approved", gate_passed=True,
                reason=f"RECONCILE [{reason}] legs no longer at Alpaca — row closed, est. realized ${realized:+,.0f}"
                       + (f"; stock position {stock_qty:+.0f} sh detected" if stock_qty else ""),
                position_id=p["id"]).model_dump())
            events.append({"underlying": p["underlying"], "reason": reason, "pnl": realized})
        orphans = [x for x in live if x.get("asset_class") == "us_option" and x["symbol"] not in ours]
        for x in orphans:
            if await self.db.reconcile_seen.find_one({"symbol": x["symbol"]}):
                continue
            await self.db.reconcile_seen.insert_one({"symbol": x["symbol"], "ts": now_iso()})
            await self.db.decisions.insert_one(Decision(
                cycle_id="reconcile", underlying=x.get("symbol", "")[:6].rstrip("0123456789"), outcome="error",
                reason=f"RECONCILE: Alpaca holds {x['symbol']} qty {x['qty']} that Petra did not open — not managed by the agent.").model_dump())
        return events

    # ---------- market data ----------
    async def get_market(self):
        m = await self.db.market.find_one({"id": "market"}, {"_id": 0})
        if not m or (datetime.now(timezone.utc) - datetime.fromisoformat(m["updated_at"])).total_seconds() > 45:
            return await self.advance_market()
        return m["symbols"]

    async def advance_market(self):
        """Refresh IEX snapshots for the universe; IV/spacing persist from the last chain load."""
        prev = await self.db.market.find_one({"id": "market"}) or {"symbols": {}}
        snaps = await self._req("GET", self.data, "/v2/stocks/snapshots",
                                params={"symbols": ",".join(UNIVERSE), "feed": "iex"})
        syms = {}
        for s, cfg in UNIVERSE.items():
            snap = snaps.get(s) or {}
            old = prev["symbols"].get(s, {})
            trade = snap.get("latestTrade") or {}
            daily = snap.get("dailyBar") or {}
            prevd = snap.get("prevDailyBar") or {}
            price = float(trade.get("p") or daily.get("c") or old.get("price") or cfg["px"])
            prev_close = float(prevd.get("c") or old.get("prev_price") or price)
            day_open = float(daily.get("o") or old.get("day_open") or price)
            syms[s] = {"price": round(price, 2), "prev_price": round(prev_close, 2),
                       "day_open": round(day_open, 2),
                       "trend": round((price / prev_close - 1) * 100, 2) if prev_close else 0.0,
                       "iv": old.get("iv", cfg["iv"]), "spacing": old.get("spacing", cfg["spacing"])}
        await self.db.market.update_one({"id": "market"}, {"$set": {"symbols": syms, "updated_at": now_iso()}}, upsert=True)
        return syms

    @staticmethod
    def _mid(q):
        bp, ap = float(q.get("bp") or 0), float(q.get("ap") or 0)
        if ap <= 0:
            return 0.0, 0.0, 0.0
        return round((bp + ap) / 2, 3), bp, ap

    async def close_values(self, open_positions, market):
        symbols = sorted({l["symbol"] for p in open_positions for l in p["legs"] if l.get("symbol")})
        quotes = {}
        for i in range(0, len(symbols), 100):
            data = await self._req("GET", self.data, "/v1beta1/options/snapshots",
                                   params={"symbols": ",".join(symbols[i:i + 100]), "feed": "indicative"})
            quotes.update(data.get("snapshots", {}))
        out = {}
        for p in open_positions:
            val, ok = 0.0, True
            for leg in p["legs"]:
                mid, _, _ = self._mid((quotes.get(leg.get("symbol")) or {}).get("latestQuote") or {})
                if mid <= 0 and leg["side"] == "sell":
                    ok = False
                val += mid if leg["side"] == "sell" else -mid
            out[p["id"]] = (round(max(0.0, val), 2), _remaining_days(p["expiry_ts"])) if ok else _bs_close_value(p, market)
        return out

    # ---------- options chain ----------
    async def _paged(self, base, path, key, params):
        items = {} if key == "snapshots" else []
        token = None
        while True:
            q = dict(params, limit=1000)
            if token:
                q["page_token"] = token
            data = await self._req("GET", base, path, params=q)
            if key == "snapshots":
                items.update(data.get(key, {}))
            else:
                items.extend(data.get(key, []))
            token = data.get("next_page_token")
            if not token:
                return items

    async def load_chain(self, underlying, S, cfg):
        """Pick the nearest expiry ≥ dte_min, pull real contracts + indicative snapshots."""
        today = datetime.now(ET).date()
        gte, lte = today + timedelta(days=cfg["dte_min"]), today + timedelta(days=cfg["dte_min"] + 14)
        probe = await self._paged(self.trading, "/options/contracts", "option_contracts", {
            "underlying_symbols": underlying, "status": "active", "type": "put",
            "expiration_date_gte": gte.isoformat(), "expiration_date_lte": lte.isoformat(),
            "strike_price_gte": str(round(S * 0.97, 2)), "strike_price_lte": str(round(S * 1.03, 2))})
        if not probe:
            return None
        exp = min(c["expiration_date"] for c in probe)
        contracts = await self._paged(self.trading, "/options/contracts", "option_contracts", {
            "underlying_symbols": underlying, "status": "active", "expiration_date": exp,
            "strike_price_gte": str(round(S * 0.88, 2)), "strike_price_lte": str(round(S * 1.12, 2))})
        snaps = await self._paged(self.data, "/v1beta1/options/snapshots/" + underlying, "snapshots", {
            "expiration_date": exp, "feed": "indicative",
            "strike_price_gte": str(round(S * 0.88, 2)), "strike_price_lte": str(round(S * 1.12, 2))})

        chain = {"put": {}, "call": {}}
        for c in contracts:
            if not c.get("tradable"):
                continue
            snap = snaps.get(c["symbol"]) or {}
            mid, bid, ask = self._mid(snap.get("latestQuote") or {})
            greeks = snap.get("greeks") or {}
            strike = float(c["strike_price"])
            chain[c["type"]][strike] = {
                "strike": strike, "mid": mid, "bid": bid, "ask": ask,
                "delta": greeks.get("delta"), "iv": snap.get("impliedVolatility"),
                "bid_ask_pct": round((ask - bid) / mid, 4) if mid > 0 else 1.0,
                "open_interest": int(float(c.get("open_interest") or 0)), "symbol": c["symbol"]}
        strikes = sorted(chain["put"])
        if len(strikes) < 4:
            return None
        diffs = sorted(round(b - a, 2) for a, b in zip(strikes, strikes[1:]) if b > a)
        spacing = diffs[len(diffs) // 2]
        atm = min(strikes, key=lambda k: abs(k - S))
        iv = chain["put"][atm].get("iv") or chain["call"].get(atm, {}).get("iv")
        y, mth, d = (int(x) for x in exp.split("-"))
        expiry_ts = datetime(y, mth, d, 16, 0, tzinfo=ET).astimezone(timezone.utc).isoformat()
        self._chains[underlying] = {**chain, "expiry_ts": expiry_ts, "spacing": spacing, "S": S}
        upd = {"symbols.%s.spacing" % underlying: spacing}
        if iv:
            upd["symbols.%s.iv" % underlying] = round(float(iv), 4)
        await self.db.market.update_one({"id": "market"}, {"$set": upd})
        return {"expiry": exp, "spacing": spacing, "iv": round(float(iv), 4) if iv else None,
                "strikes": len(strikes)}

    def expiry_ts(self, underlying, dte):
        return self._chains[underlying]["expiry_ts"]

    def build_chain_leg(self, underlying, S, iv, opt_type, strike, T):
        legs = self._chains[underlying][opt_type]
        k = min(legs, key=lambda x: abs(x - strike))
        return legs[k]

    def find_strike_by_delta(self, underlying, S, iv, spacing, opt_type, target_delta, T):
        legs = self._chains[underlying][opt_type]
        otm = [l for k, l in legs.items() if (k > S if opt_type == "call" else k < S)
               and l["delta"] is not None and l["mid"] > 0]
        if not otm:
            return None
        return min(otm, key=lambda l: abs(abs(l["delta"]) - target_delta))

    # ---------- orders ----------
    async def _await_fill(self, order_id, wait_s=15):
        for _ in range(wait_s // 3):
            await asyncio.sleep(3)
            o = await self._req("GET", self.trading, f"/orders/{order_id}")
            if o["status"] == "filled":
                return o
            if o["status"] in ("canceled", "rejected", "expired"):
                return o
        try:
            await self._req("DELETE", self.trading, f"/orders/{order_id}")
        except RuntimeError:
            pass
        o = await self._req("GET", self.trading, f"/orders/{order_id}")
        return o

    async def _submit(self, payload, meta):
        try:
            o = await self._req("POST", self.trading, "/orders", json=payload)
        except RuntimeError as e:
            res = {"order_id": "", "status": "error", "alpaca_status": "rejected", "filled_price": 0.0, "error": str(e)[:300]}
            await log_order(self.db, {**meta, "error": res["error"]}, payload, res)
            return res
        o = await self._await_fill(o["id"])
        px = abs(float(o.get("filled_avg_price") or 0))
        res = {"order_id": o["id"], "status": o["status"] if o["status"] == "filled" else "unfilled",
               "alpaca_status": o["status"], "filled_price": px}
        await log_order(self.db, meta, payload, res)
        return res

    async def place_mleg(self, proposal, cycle_id=""):
        res = await self._submit(_open_payload(proposal, cycle_id), {
            "intent": "open", "underlying": proposal["underlying"], "strategy": proposal["strategy"],
            "cycle_id": cycle_id, "mode": "live"})
        res["filled_credit"] = res.pop("filled_price")
        return res

    async def close_mleg(self, position, urgent=False, reason=""):
        res = await self._submit(_close_payload(position, urgent), {
            "intent": "close", "underlying": position["underlying"], "strategy": position["strategy"],
            "reason": reason, "position_id": position["id"], "mode": "live"})
        res["filled_debit"] = res.pop("filled_price")
        return res


def make_alpaca(db):
    return LiveAlpaca(db) if MODE == "live" else MockAlpaca(db)

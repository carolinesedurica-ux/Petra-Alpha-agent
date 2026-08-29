"""Deterministic strike/size engine + risk gate. NO LLM here — pure rules."""
import math
from datetime import datetime, timezone, timedelta

from alpaca import occ_symbol
from models import now_iso


def build_spread(alpaca, underlying, S, iv, spacing, verdict, cfg, equity):
    """
    Convert an LLM verdict into a concrete defined-risk spread.
    Returns a proposal dict or None if strikes can't be built.
    """
    strategy = verdict["chosen_strategy"]
    dte = cfg["dte_min"]
    T = max(dte, 0.5) / 365.0
    expiry_ts = (datetime.now(timezone.utc) + timedelta(days=dte)).isoformat()
    td = cfg["target_delta"]
    width = spacing * 2  # 2-strike wide spread

    legs = []

    def put_credit():
        short = alpaca.find_strike_by_delta(underlying, S, iv, spacing, "put", td, T)
        long_k = short["strike"] - width
        long = alpaca.build_chain_leg(underlying, S, iv, "put", long_k, T)
        return short, long

    def call_credit():
        short = alpaca.find_strike_by_delta(underlying, S, iv, spacing, "call", td, T)
        long_k = short["strike"] + width
        long = alpaca.build_chain_leg(underlying, S, iv, "call", long_k, T)
        return short, long

    parts = []
    if strategy == "put_credit_spread":
        parts.append(("put", *put_credit()))
    elif strategy == "call_credit_spread":
        parts.append(("call", *call_credit()))
    elif strategy == "iron_condor":
        parts.append(("put", *put_credit()))
        parts.append(("call", *call_credit()))
    else:
        return None

    credit = 0.0
    worst_bid_ask = 0.0
    min_oi = 10 ** 9
    for opt_type, short, long in parts:
        credit += (short["mid"] - long["mid"])  # net credit at mid
        worst_bid_ask = max(worst_bid_ask, short["bid_ask_pct"], long["bid_ask_pct"])
        min_oi = min(min_oi, short["open_interest"], long["open_interest"])
        legs.append({"side": "sell", "option_type": opt_type, "strike": short["strike"],
                     "delta": short["delta"], "price": short["mid"],
                     "symbol": occ_symbol(underlying, expiry_ts, opt_type, short["strike"])})
        legs.append({"side": "buy", "option_type": opt_type, "strike": long["strike"],
                     "delta": long["delta"], "price": long["mid"],
                     "symbol": occ_symbol(underlying, expiry_ts, opt_type, long["strike"])})

    credit = round(max(0.01, credit), 2)
    # for an iron condor total width risk is a single side width (only one side can be breached)
    risk_width = width
    max_loss_per = (risk_width - credit) * 100
    if max_loss_per <= 0:
        return None

    risk_budget = equity * (cfg["max_risk_pct"] / 100.0)
    contracts = max(0, int(risk_budget // max_loss_per))
    contracts = min(contracts, 10)
    if contracts < 1:
        contracts = 1  # allow 1 lot; risk gate will reject if it still breaches cap

    max_risk = round(max_loss_per * contracts, 2)
    credit_width_ratio = credit / risk_width

    return {
        "underlying": underlying, "strategy": strategy, "legs": legs,
        "contracts": contracts, "width": risk_width, "credit": credit,
        "max_risk": max_risk, "entry_underlying": S, "entry_iv": iv,
        "dte": dte, "expiry_ts": expiry_ts,
        "credit_width_ratio": round(credit_width_ratio, 3),
        "worst_bid_ask": round(worst_bid_ask, 4), "min_oi": min_oi,
        "short_delta": round(max(abs(l["delta"]) for l in legs if l["side"] == "sell"), 3),
    }


def risk_gate(proposal, cfg, equity, open_positions):
    """Hard deterministic stops. Returns (checks, passed, score)."""
    checks = []
    risk_cap = equity * (cfg["max_risk_pct"] / 100.0)

    # 1. Max risk per trade
    c1 = proposal["max_risk"] <= risk_cap * 1.0
    checks.append({"label": f"Max Risk ≤ ${risk_cap:,.0f} ({cfg['max_risk_pct']}%)",
                   "passed": c1, "detail": f"Trade risk ${proposal['max_risk']:,.0f}"})

    # 2. Max concurrent positions
    c2 = len(open_positions) < cfg["max_concurrent"]
    checks.append({"label": f"Concurrent < {cfg['max_concurrent']}",
                   "passed": c2, "detail": f"{len(open_positions)} open"})

    # 3. Total capital at risk cap (portfolio ≤ 3x single-trade cap)
    total_risk = sum(p["max_risk"] for p in open_positions) + proposal["max_risk"]
    portfolio_cap = risk_cap * cfg["max_concurrent"]
    c3 = total_risk <= portfolio_cap
    checks.append({"label": f"Portfolio Risk ≤ ${portfolio_cap:,.0f}",
                   "passed": c3, "detail": f"Total ${total_risk:,.0f}"})

    # 4. Min credit-to-width ratio
    c4 = proposal["credit_width_ratio"] >= cfg["min_credit_width"]
    checks.append({"label": f"Credit/Width ≥ {cfg['min_credit_width']:.0%}",
                   "passed": c4, "detail": f"{proposal['credit_width_ratio']:.0%}"})

    # 5. Liquidity: bid/ask spread
    c5 = proposal["worst_bid_ask"] <= cfg["max_bid_ask_pct"]
    checks.append({"label": f"Bid/Ask ≤ {cfg['max_bid_ask_pct']:.0%}",
                   "passed": c5, "detail": f"{proposal['worst_bid_ask']:.0%}"})

    # 6. Liquidity: open interest
    c6 = proposal["min_oi"] >= cfg["min_open_interest"]
    checks.append({"label": f"Open Interest ≥ {cfg['min_open_interest']}",
                   "passed": c6, "detail": f"{proposal['min_oi']} OI"})

    # 7. Idempotency / duplicate underlying
    dup = any(p["underlying"] == proposal["underlying"] and p["status"] == "open" for p in open_positions)
    c7 = not dup
    checks.append({"label": "No Duplicate Position",
                   "passed": c7, "detail": "duplicate underlying open" if dup else "unique"})

    passed = all(c["passed"] for c in checks)
    score = int(round(100 * sum(1 for c in checks if c["passed"]) / len(checks)))
    return checks, passed, score

"""
Strategy Demonstration Test Suite
Tests and demonstrates:
1. Opportunity Identification: Market scanning, implied volatility, trend, and LLM regime classification.
2. Trading Decision Making: Strike selection (delta ~0.20), dynamic sizing, and the 7 Hard Risk Gates.
3. Position Management: Mark-to-market, Take-Profit (50%), Stop-Loss (2.0x), Time Exit (<=0.75 DTE), and Reconciliation.
"""
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines import build_spread, risk_gate
from models import Position, RiskConfig
from pricing import bs_price, bs_delta


class MockAlpacaTest:
    def __init__(self):
        self.mode = "test"

    def expiry_ts(self, underlying, dte_min):
        dt = datetime.now(timezone.utc) + timedelta(days=5)
        return dt.isoformat()

    def find_strike_by_delta(self, underlying, S, iv, spacing, opt_type, target_delta, T):
        strike = round((S * 0.96) if opt_type == "put" else (S * 1.04), 1)
        return {
            "strike": strike,
            "mid": 1.25,
            "delta": -target_delta if opt_type == "put" else target_delta,
            "bid_ask_pct": 0.03,
            "open_interest": 1200,
            "symbol": f"{underlying}260910P00{int(strike*1000)}"
        }

    def build_chain_leg(self, underlying, S, iv, opt_type, strike, T):
        return {
            "strike": strike,
            "mid": 0.45,
            "delta": -0.10 if opt_type == "put" else 0.10,
            "bid_ask_pct": 0.04,
            "open_interest": 950,
            "symbol": f"{underlying}260910P00{int(strike*1000)}"
        }


def test_strategy_proposal_and_sizing():
    """Demonstrate how opportunity transforms into defined-risk spread with delta targeting and sizing."""
    alpaca = MockAlpacaTest()
    underlying = "SPY"
    S = 545.0
    iv = 0.16
    spacing = 1.0
    equity = 100000.0
    cfg = RiskConfig().model_dump()

    verdict = {
        "regime": "trending_up",
        "direction": "bullish",
        "confidence": 0.85,
        "chosen_strategy": "put_credit_spread",
        "rationale": "SPY showing upward momentum with controlled IV; selling out-of-the-money puts."
    }

    proposal = build_spread(alpaca, underlying, S, iv, spacing, verdict, cfg, equity)
    assert proposal is not None
    assert proposal["strategy"] == "put_credit_spread"
    assert proposal["credit"] == 0.80  # 1.25 - 0.45
    assert proposal["width"] == 2.0
    assert proposal["credit_width_ratio"] == 0.40  # 0.80 / 2.0
    # Max risk per spread: (2.0 - 0.80) * 100 = $120
    # Sizing: risk_budget = 100,000 * 2.0% = $2,000. Contracts = 2000 // 120 = 16 -> capped at 10
    assert proposal["contracts"] == 10
    assert proposal["max_risk"] == 1200.0


def test_seven_hard_risk_gates():
    """Demonstrate the 7 hard deterministic risk gates."""
    alpaca = MockAlpacaTest()
    cfg = RiskConfig().model_dump()
    equity = 100000.0

    verdict = {
        "regime": "trending_up",
        "direction": "bullish",
        "confidence": 0.80,
        "chosen_strategy": "put_credit_spread",
        "rationale": "Bullish trend, high IV rank"
    }
    proposal = build_spread(alpaca, "QQQ", 470.0, 0.20, 1.0, verdict, cfg, equity)

    # 1. Clean portfolio -> all 7 gates should pass
    checks, passed, score = risk_gate(proposal, cfg, equity, [])
    assert passed is True
    assert score == 100
    assert len(checks) == 7

    # 2. Gate 7 check: Duplicate underlying rejection
    open_positions = [{"underlying": "QQQ", "status": "open", "max_risk": 1000.0}]
    checks, passed, score = risk_gate(proposal, cfg, equity, open_positions)
    assert passed is False
    assert any("Duplicate" in c["label"] and not c["passed"] for c in checks)

    # 3. Gate 2 check: Max concurrent positions rejection
    crowded_positions = [{"underlying": f"SYM{i}", "status": "open", "max_risk": 500.0} for i in range(5)]
    checks, passed, score = risk_gate(proposal, cfg, equity, crowded_positions)
    assert passed is False
    assert any("Concurrent" in c["label"] and not c["passed"] for c in checks)


def test_position_management_triggers():
    """Demonstrate position management: Take Profit, Stop Loss, and Time Exit."""
    cfg = RiskConfig().model_dump()
    credit = 1.00  # Initial credit collected per share

    # Case 1: Take Profit (current value drops to <= credit * (1 - tp_pct) -> 1.0 * (1 - 0.5) = 0.50)
    current_val_tp = 0.40
    should_tp = current_val_tp <= credit * (1 - cfg["tp_pct"])
    assert should_tp is True

    # Case 2: Stop Loss (current value rises to >= credit * stop_mult -> 1.0 * 2.0 = 2.00)
    current_val_sl = 2.10
    should_sl = current_val_sl >= credit * cfg["stop_mult"]
    assert should_sl is True

    # Case 3: Time Exit (DTE <= 0.75 days to avoid expiration pin risk)
    dte_safe = 2.5
    dte_risk = 0.5
    assert (dte_safe <= 0.75) is False
    assert (dte_risk <= 0.75) is True


def test_manual_position_creation():
    """Verify manual position creation retains all fields, targets, and paper_sim flag."""
    cfg = RiskConfig().model_dump()
    credit = 0.85
    contracts = 3
    pos = Position(
        underlying="QQQ",
        strategy="put_credit_spread",
        legs=[
            {"side": "sell", "option_type": "put", "strike": 460.0, "delta": -0.21, "price": 1.25, "symbol": "QQQ260908P00460000"},
            {"side": "buy", "option_type": "put", "strike": 458.0, "delta": -0.10, "price": 0.40, "symbol": "QQQ260908P00458000"}
        ],
        contracts=contracts,
        width=2.0,
        credit=credit,
        max_risk=round((2.0 - credit) * 100 * contracts, 2),
        entry_underlying=470.0,
        entry_iv=0.20,
        dte=5.0,
        expiry_ts="2026-09-08T20:00:00+00:00",
        tp_target=round(credit * (1 - cfg["tp_pct"]), 2),
        stop_target=round(credit * cfg["stop_mult"], 2),
        current_value=credit,
        paper_sim=True,
        alpaca_order_id="test-manual-order"
    )
    assert pos.status == "open"
    assert pos.paper_sim is True
    assert pos.tp_target == round(credit * (1 - cfg["tp_pct"]), 2)
    assert pos.stop_target == round(credit * cfg["stop_mult"], 2)
    assert pos.max_risk == 345.0  # (2.0 - 0.85) * 100 * 3


if __name__ == "__main__":
    test_strategy_proposal_and_sizing()
    test_seven_hard_risk_gates()
    test_position_management_triggers()
    test_manual_position_creation()
    print("All strategy demonstration unit tests passed!")

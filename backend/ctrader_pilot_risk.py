"""Pure risk checks for Petra's cTrader micro-live pilot.

This module does not connect to a broker and cannot place orders. It evaluates
broker-reported symbol/margin metadata against a deliberately small pilot budget.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PilotRiskPolicy:
    pilot_capital_usd: float = 20.0
    max_margin_per_trade_usd: float = 5.0
    max_margin_fraction_of_balance: float = 0.25
    max_open_positions: int = 1
    max_pending_orders: int = 0
    daily_loss_stop_usd: float = 1.0


@dataclass(frozen=True)
class CandidateAssessment:
    eligible: bool
    reason: str
    conservative_margin_usd: float
    margin_limit_usd: float


def assess_candidate(
    *,
    balance_usd: float,
    buy_margin_usd: float,
    sell_margin_usd: float,
    open_positions: int,
    pending_orders: int,
    realized_daily_pnl_usd: float = 0.0,
    policy: PilotRiskPolicy = PilotRiskPolicy(),
) -> CandidateAssessment:
    """Fail-closed assessment for one broker-minimum-size candidate.

    The larger of buy/sell expected margin is used so Petra never chooses the
    cheaper side merely to pass the gate. This function intentionally does not
    consider expected profit; it only answers whether a minimum-size trade is
    mechanically small enough for the pilot.
    """
    if balance_usd <= 0:
        return CandidateAssessment(False, "non-positive account balance", 0.0, 0.0)

    margin_limit = min(
        policy.max_margin_per_trade_usd,
        balance_usd * policy.max_margin_fraction_of_balance,
        policy.pilot_capital_usd * policy.max_margin_fraction_of_balance,
    )
    conservative_margin = max(float(buy_margin_usd), float(sell_margin_usd))

    if open_positions >= policy.max_open_positions:
        return CandidateAssessment(False, "open-position cap reached", conservative_margin, margin_limit)
    if pending_orders > policy.max_pending_orders:
        return CandidateAssessment(False, "pending order exists", conservative_margin, margin_limit)
    if realized_daily_pnl_usd <= -policy.daily_loss_stop_usd:
        return CandidateAssessment(False, "daily loss stop reached", conservative_margin, margin_limit)
    if conservative_margin <= 0:
        return CandidateAssessment(False, "broker returned non-positive expected margin", conservative_margin, margin_limit)
    if conservative_margin > margin_limit:
        return CandidateAssessment(False, "minimum-size margin exceeds pilot limit", conservative_margin, margin_limit)

    return CandidateAssessment(True, "minimum-size margin fits pilot policy", conservative_margin, margin_limit)

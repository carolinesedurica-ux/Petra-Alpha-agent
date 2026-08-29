"""Pydantic models. JSON-safe uuid ids; Mongo _id excluded on reads."""
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Literal, Any
from pydantic import BaseModel, Field, ConfigDict


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return str(uuid.uuid4())


class Leg(BaseModel):
    side: Literal["sell", "buy"]
    option_type: Literal["put", "call"]
    strike: float
    delta: float
    price: float  # per-share option premium
    symbol: str  # OCC-style occ symbol


class Position(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=new_id)
    underlying: str
    strategy: str  # put_credit_spread | call_credit_spread | iron_condor
    legs: List[Leg]
    contracts: int
    width: float
    credit: float           # net credit collected per spread (per share)
    max_risk: float         # total dollar risk for the position
    entry_underlying: float
    entry_iv: float
    dte: float              # days to expiry at entry
    expiry_ts: str
    tp_target: float        # net debit to close for take-profit
    stop_target: float      # net debit to close for stop-loss
    current_value: float    # current net debit to close (per share)
    unrealized_pnl: float = 0.0
    unrealized_pct: float = 0.0
    risk_gate_score: int = 100
    alpaca_order_id: str = ""
    status: Literal["open", "closed"] = "open"
    exit_reason: Optional[str] = None
    realized_pnl: float = 0.0
    opened_at: str = Field(default_factory=now_iso)
    closed_at: Optional[str] = None


class Verdict(BaseModel):
    regime: str
    direction: str
    confidence: float
    chosen_strategy: str
    rationale: str
    source: str = "llm"  # llm | fallback


class GateCheck(BaseModel):
    label: str
    passed: bool
    detail: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=new_id)
    cycle_id: str
    underlying: str
    verdict: Optional[dict] = None
    strategy: Optional[str] = None
    proposed: Optional[dict] = None   # proposed spread summary
    gate_checks: List[dict] = []
    gate_passed: bool = False
    outcome: str = "rejected"         # approved | rejected | skipped | error
    reason: str = ""
    position_id: Optional[str] = None
    market_snapshot: Optional[dict] = None
    created_at: str = Field(default_factory=now_iso)


class RiskConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "risk_config"
    max_risk_pct: float = 2.0
    max_concurrent: int = 5
    min_credit_width: float = 0.18
    target_delta: float = 0.22
    dte_min: int = 3
    dte_max: int = 7
    tp_pct: float = 0.50
    stop_mult: float = 2.0
    max_bid_ask_pct: float = 0.15
    min_open_interest: int = 500
    aggressiveness: str = "balanced"


class AgentState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "agent_state"
    autonomous: bool = True
    paused: bool = False
    last_cycle_at: Optional[str] = None
    total_cycles: int = 0

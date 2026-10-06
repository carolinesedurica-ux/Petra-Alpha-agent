"""Offline round-trip state-machine simulation for Petra's cTrader adapter.

This module does not connect to cTrader and cannot place, modify, or close orders.
It validates the control flow Petra will require around a future broker execution
adapter: preflight flat-account checks, USD20 pilot margin gate, one-position cap,
protective-stop requirement, exact-volume reconciliation, kill switch, and final
flat-account verification.
"""
from __future__ import annotations

from dataclasses import dataclass

from ctrader_pilot_risk import assess_candidate


@dataclass(frozen=True)
class SimPosition:
    position_id: int
    symbol: str
    volume_cents: int
    entry_price: float
    stop_loss: float | None


@dataclass
class RoundTripState:
    stage: str = "preflight"
    kill_switch: bool = False
    position: SimPosition | None = None
    entry_order_id: int | None = None
    closed: bool = False

    def preflight(self, *, open_positions: int, pending_orders: int) -> None:
        if self.kill_switch:
            raise RuntimeError("kill switch active")
        if open_positions != 0 or pending_orders != 0:
            raise RuntimeError("account must be flat with zero pending orders")
        self.stage = "risk_gate"

    def risk_gate(self, *, buy_margin_usd: float, sell_margin_usd: float) -> None:
        if self.stage != "risk_gate":
            raise RuntimeError("risk gate called out of sequence")
        assessment = assess_candidate(
            balance_usd=20.0,
            buy_margin_usd=buy_margin_usd,
            sell_margin_usd=sell_margin_usd,
            open_positions=0,
            pending_orders=0,
        )
        if not assessment.eligible:
            raise RuntimeError(assessment.reason)
        self.stage = "entry_planned"

    def record_fill(
        self,
        *,
        position_id: int,
        order_id: int,
        symbol: str,
        requested_volume_cents: int,
        filled_volume_cents: int,
        entry_price: float,
        stop_loss: float | None,
    ) -> None:
        if self.stage != "entry_planned":
            raise RuntimeError("fill received out of sequence")
        if self.kill_switch:
            raise RuntimeError("kill switch active")
        if filled_volume_cents != requested_volume_cents:
            raise RuntimeError("filled volume does not match requested volume")
        if stop_loss is None or stop_loss <= 0:
            raise RuntimeError("position has no broker-side protective stop")
        self.position = SimPosition(
            position_id=position_id,
            symbol=symbol,
            volume_cents=filled_volume_cents,
            entry_price=entry_price,
            stop_loss=stop_loss,
        )
        self.entry_order_id = order_id
        self.stage = "position_open"

    def request_close(self, *, position_id: int, volume_cents: int) -> None:
        if self.stage != "position_open" or self.position is None:
            raise RuntimeError("no managed open position")
        if self.kill_switch:
            raise RuntimeError("kill switch active")
        if position_id != self.position.position_id:
            raise RuntimeError("position id mismatch")
        if volume_cents != self.position.volume_cents:
            raise RuntimeError("close volume mismatch")
        self.stage = "close_planned"

    def record_close(self, *, position_id: int) -> None:
        if self.stage != "close_planned" or self.position is None:
            raise RuntimeError("close received out of sequence")
        if position_id != self.position.position_id:
            raise RuntimeError("closed a different position")
        self.closed = True
        self.stage = "final_reconcile"

    def final_reconcile(self, *, open_positions: int, pending_orders: int) -> None:
        if self.stage != "final_reconcile":
            raise RuntimeError("final reconcile called out of sequence")
        if open_positions != 0 or pending_orders != 0:
            raise RuntimeError("account not flat after close")
        self.stage = "complete"

    def trip_kill_switch(self) -> None:
        self.kill_switch = True

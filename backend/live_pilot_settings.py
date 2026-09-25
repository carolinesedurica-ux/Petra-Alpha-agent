"""Strict configuration for Petra's tiny funded-equity execution pilot.

This module is intentionally separate from the options/paper worker. It has no options,
shorting, margin, or funded multi-leg path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


LIVE_TRADING_URL = "https://api.alpaca.markets/v2"
ALPACA_DATA_URL = "https://data.alpaca.markets"


class LivePilotSettingsError(RuntimeError):
    pass


def _raw(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _required(name: str) -> str:
    value = _raw(name)
    if not value:
        raise LivePilotSettingsError(f"{name} is required")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name)
    if not raw:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise LivePilotSettingsError(f"{name} must be true or false")


def _float(name: str, default: float, lo: float, hi: float) -> float:
    raw = _raw(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise LivePilotSettingsError(f"{name} must be numeric") from exc
    if not lo <= value <= hi:
        raise LivePilotSettingsError(f"{name} must be between {lo} and {hi}")
    return value


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _raw(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise LivePilotSettingsError(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise LivePilotSettingsError(f"{name} must be between {lo} and {hi}")
    return value


@dataclass(frozen=True)
class LivePilotSettings:
    api_key: str
    api_secret: str
    expected_account_number: str
    mongo_url: str
    db_name: str
    dry_run: bool
    armed: bool
    execution_confirm: str
    symbols: tuple[str, ...]
    trade_notional_usd: float
    max_allocation_pct: float
    daily_loss_usd: float
    stop_loss_pct: float
    take_profit_pct: float
    max_hold_minutes: int
    entry_momentum_pct: float
    github_run_id: str
    github_run_attempt: str

    @property
    def run_id(self) -> str:
        return f"{self.github_run_id or 'manual'}-{self.github_run_attempt or '1'}"

    @property
    def can_submit(self) -> bool:
        return (not self.dry_run and self.armed and self.execution_confirm == "LIVE_PILOT")


def load_live_pilot_settings() -> LivePilotSettings:
    endpoint = (_raw("ALPACA_LIVE_TRADING_URL") or LIVE_TRADING_URL).rstrip("/")
    if endpoint != LIVE_TRADING_URL:
        raise LivePilotSettingsError(f"ALPACA_LIVE_TRADING_URL must be exactly {LIVE_TRADING_URL}")

    if _bool("ALLOW_LIVE_TRADING", False):
        raise LivePilotSettingsError(
            "Generic ALLOW_LIVE_TRADING must remain false; the micro pilot uses its own isolated arming gate"
        )

    symbols = tuple(
        s.strip().upper() for s in (_raw("PETRA_LIVE_SYMBOLS") or "SPY,QQQ").split(",") if s.strip()
    )
    if not symbols or len(symbols) > 3:
        raise LivePilotSettingsError("PETRA_LIVE_SYMBOLS must contain 1 to 3 symbols")
    allowed = {"SPY", "QQQ", "IWM"}
    if any(s not in allowed for s in symbols):
        raise LivePilotSettingsError(f"Live pilot symbols must be drawn from {sorted(allowed)}")

    dry_run = _bool("PETRA_LIVE_DRY_RUN", True)
    armed = _bool("PETRA_LIVE_PILOT_ARMED", False)
    confirm = _raw("PETRA_LIVE_EXECUTION_CONFIRM")
    if not dry_run and (not armed or confirm != "LIVE_PILOT"):
        raise LivePilotSettingsError(
            "Funded pilot submission requires PETRA_LIVE_PILOT_ARMED=true and "
            "PETRA_LIVE_EXECUTION_CONFIRM=LIVE_PILOT"
        )

    db_name = _raw("PETRA_LIVE_DB_NAME") or "petra_live_pilot"
    if any(ch in db_name for ch in '/\\." $'):
        raise LivePilotSettingsError("PETRA_LIVE_DB_NAME contains invalid MongoDB database-name characters")

    return LivePilotSettings(
        api_key=_required("ALPACA_LIVE_API_KEY"),
        api_secret=_required("ALPACA_LIVE_SECRET_KEY"),
        expected_account_number=_required("ALPACA_LIVE_EXPECTED_ACCOUNT_NUMBER"),
        mongo_url=_required("PETRA_LIVE_MONGO_URL"),
        db_name=db_name,
        dry_run=dry_run,
        armed=armed,
        execution_confirm=confirm,
        symbols=symbols,
        trade_notional_usd=_float("PETRA_LIVE_TRADE_NOTIONAL_USD", 5.0, 1.0, 10.0),
        max_allocation_pct=_float("PETRA_LIVE_MAX_ALLOCATION_PCT", 20.0, 2.0, 25.0),
        daily_loss_usd=_float("PETRA_LIVE_DAILY_LOSS_USD", 1.0, 0.10, 2.50),
        stop_loss_pct=_float("PETRA_LIVE_STOP_LOSS_PCT", 0.60, 0.10, 2.00),
        take_profit_pct=_float("PETRA_LIVE_TAKE_PROFIT_PCT", 0.80, 0.10, 3.00),
        max_hold_minutes=_int("PETRA_LIVE_MAX_HOLD_MINUTES", 120, 15, 360),
        entry_momentum_pct=_float("PETRA_LIVE_ENTRY_MOMENTUM_PCT", 0.15, 0.05, 1.00),
        github_run_id=_raw("GITHUB_RUN_ID"),
        github_run_attempt=_raw("GITHUB_RUN_ATTEMPT"),
    )

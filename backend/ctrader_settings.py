"""Strict cTrader settings for Petra's broker-adapter pilot.

The cTrader path is isolated from the existing Alpaca micro-live path. It starts
read-only and fail-closed. Live order submission is not enabled by this module;
the arming fields are defined now so a later execution adapter can reuse the same
explicit two-key gate without relaxing safety controls.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class CTraderSettingsError(RuntimeError):
    pass


def _raw(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _required(name: str) -> str:
    value = _raw(name)
    if not value:
        raise CTraderSettingsError(f"{name} is required")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = _raw(name)
    if not raw:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise CTraderSettingsError(f"{name} must be true or false")


@dataclass(frozen=True)
class CTraderSettings:
    environment: str
    client_id: str
    client_secret: str
    access_token: str
    expected_account_id: int
    dry_run: bool
    armed: bool
    execution_confirm: str

    @property
    def is_live_endpoint(self) -> bool:
        return self.environment == "live"

    @property
    def can_submit(self) -> bool:
        return (
            self.is_live_endpoint
            and not self.dry_run
            and self.armed
            and self.execution_confirm == "CTRADER_LIVE_PILOT"
        )


def load_ctrader_settings() -> CTraderSettings:
    environment = (_raw("PETRA_CTRADER_ENV") or "demo").lower()
    if environment not in {"demo", "live"}:
        raise CTraderSettingsError("PETRA_CTRADER_ENV must be demo or live")

    # Petra's generic Alpaca live switch must remain false. Each funded broker
    # adapter has its own isolated arming gate.
    if _bool("ALLOW_LIVE_TRADING", False):
        raise CTraderSettingsError(
            "Generic ALLOW_LIVE_TRADING must remain false; cTrader uses an isolated pilot gate"
        )

    raw_account_id = _required("CTRADER_EXPECTED_ACCOUNT_ID")
    try:
        expected_account_id = int(raw_account_id)
    except ValueError as exc:
        raise CTraderSettingsError("CTRADER_EXPECTED_ACCOUNT_ID must be an integer") from exc
    if expected_account_id <= 0:
        raise CTraderSettingsError("CTRADER_EXPECTED_ACCOUNT_ID must be positive")

    dry_run = _bool("PETRA_CTRADER_DRY_RUN", True)
    armed = _bool("PETRA_CTRADER_PILOT_ARMED", False)
    confirm = _raw("PETRA_CTRADER_EXECUTION_CONFIRM")

    if environment == "live" and not dry_run:
        if not armed or confirm != "CTRADER_LIVE_PILOT":
            raise CTraderSettingsError(
                "Funded cTrader submission requires PETRA_CTRADER_PILOT_ARMED=true and "
                "PETRA_CTRADER_EXECUTION_CONFIRM=CTRADER_LIVE_PILOT"
            )

    return CTraderSettings(
        environment=environment,
        client_id=_required("CTRADER_CLIENT_ID"),
        client_secret=_required("CTRADER_CLIENT_SECRET"),
        access_token=_required("CTRADER_ACCESS_TOKEN"),
        expected_account_id=expected_account_id,
        dry_run=dry_run,
        armed=armed,
        execution_confirm=confirm,
    )

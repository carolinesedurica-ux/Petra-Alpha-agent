"""Strict, paper-only configuration for the one-shot Petra worker.

This module intentionally does NOT load .env files. GitHub Environment secrets/variables
are the source of truth for the worker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


PAPER_TRADING_URL = "https://paper-api.alpaca.markets/v2"
ALPACA_DATA_URL = "https://data.alpaca.markets"


class SettingsError(RuntimeError):
    pass


def _raw(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _required(name: str) -> str:
    value = _raw(name)
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _bool(name: str, default: bool) -> bool:
    value = _raw(name)
    if not value:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} must be true or false")


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = _raw(name)
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return parsed


@dataclass(frozen=True)
class WorkerSettings:
    broker_env: str
    alpaca_key: str
    alpaca_secret: str
    expected_account_number: str
    mongo_url: str
    db_name: str
    featherless_key: str | None
    options_feed: str
    entries_enabled: bool
    dry_run: bool
    max_candidates: int
    lease_ttl_seconds: int
    github_run_id: str
    github_run_attempt: str

    @property
    def run_id(self) -> str:
        base = self.github_run_id or "manual"
        attempt = self.github_run_attempt or "1"
        return f"{base}-{attempt}"


def load_settings() -> WorkerSettings:
    broker_env = _required("PETRA_BROKER_ENV").lower()
    if broker_env != "paper":
        raise SettingsError("PETRA_BROKER_ENV must be exactly 'paper'")

    # There is deliberately no funded/live execution path in this worker.
    if _bool("ALLOW_LIVE_TRADING", False):
        raise SettingsError("ALLOW_LIVE_TRADING must remain false for the paper worker")

    configured_url = _raw("ALPACA_TRADING_URL")
    if configured_url and configured_url.rstrip("/") != PAPER_TRADING_URL:
        raise SettingsError(
            f"ALPACA_TRADING_URL must be exactly {PAPER_TRADING_URL} for this worker"
        )

    key = _required("ALPACA_PAPER_API_KEY")
    secret = _required("ALPACA_PAPER_SECRET_KEY")
    expected = _required("ALPACA_EXPECTED_ACCOUNT_NUMBER")
    if not expected.upper().startswith("PA"):
        raise SettingsError("Expected account does not look like an Alpaca paper account")

    mongo_url = _required("MONGO_URL")
    db_name = _required("DB_NAME")
    if any(ch in db_name for ch in '/\\." $'):
        raise SettingsError("DB_NAME contains invalid MongoDB database-name characters")

    options_feed = (_raw("ALPACA_OPTIONS_FEED") or "indicative").lower()
    if options_feed not in {"indicative", "opra"}:
        raise SettingsError("ALPACA_OPTIONS_FEED must be 'indicative' or 'opra'")

    return WorkerSettings(
        broker_env=broker_env,
        alpaca_key=key,
        alpaca_secret=secret,
        expected_account_number=expected,
        mongo_url=mongo_url,
        db_name=db_name,
        featherless_key=_raw("FEATHERLESS_API_KEY") or None,
        options_feed=options_feed,
        entries_enabled=_bool("PETRA_ENTRIES_ENABLED", False),
        dry_run=_bool("PETRA_DRY_RUN", True),
        max_candidates=_int("TICK_MAX_CANDIDATES", 1, 1, 3),
        lease_ttl_seconds=_int("PETRA_LEASE_TTL_SECONDS", 900, 600, 1800),
        github_run_id=_raw("GITHUB_RUN_ID"),
        github_run_attempt=_raw("GITHUB_RUN_ATTEMPT"),
    )


def install_legacy_env(settings: WorkerSettings) -> None:
    """Populate the names consumed by the existing broker/agent modules.

    Values are set from already-validated paper-only settings. This lets the worker
    reuse the strategy engine while keeping GitHub secrets under paper-specific names.
    """
    os.environ["ALPACA_MODE"] = "live"  # historical Petra name = Alpaca-backed, not funded.
    os.environ["ALPACA_API_KEY"] = settings.alpaca_key
    os.environ["ALPACA_SECRET_KEY"] = settings.alpaca_secret
    os.environ["ALPACA_TRADING_URL"] = PAPER_TRADING_URL
    os.environ["ALPACA_DATA_URL"] = ALPACA_DATA_URL
    os.environ["ALPACA_OPTIONS_FEED"] = settings.options_feed
    os.environ["ALLOW_LIVE_TRADING"] = "false"
    os.environ["ALPACA_EXPECTED_ACCOUNT_NUMBER"] = settings.expected_account_number
    os.environ["ENABLE_MANUAL_EQUITY_TRADING"] = "false"
    if settings.featherless_key:
        os.environ["FEATHERLESS_API_KEY"] = settings.featherless_key

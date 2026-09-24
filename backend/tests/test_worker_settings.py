import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from settings import PAPER_TRADING_URL, SettingsError, load_settings


BASE = {
    "PETRA_BROKER_ENV": "paper",
    "ALLOW_LIVE_TRADING": "false",
    "ALPACA_PAPER_API_KEY": "paper-key",
    "ALPACA_PAPER_SECRET_KEY": "paper-secret",
    "ALPACA_EXPECTED_ACCOUNT_NUMBER": "PA_TEST_ACCOUNT",
    "MONGO_URL": "mongodb://example.invalid:27017",
    "DB_NAME": "petra_test",
}


def apply(monkeypatch, extra=None):
    for key in list(os.environ):
        if key.startswith("PETRA_") or key.startswith("ALPACA_") or key in {
            "MONGO_URL", "DB_NAME", "TICK_MAX_CANDIDATES", "FEATHERLESS_API_KEY"
        }:
            monkeypatch.delenv(key, raising=False)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    for key, value in (extra or {}).items():
        monkeypatch.setenv(key, value)


def test_defaults_fail_safe(monkeypatch):
    apply(monkeypatch)
    s = load_settings()
    assert s.broker_env == "paper"
    assert s.dry_run is True
    assert s.entries_enabled is False
    assert s.max_candidates == 1


def test_refuses_funded_trading_switch(monkeypatch):
    apply(monkeypatch, {"ALLOW_LIVE_TRADING": "true"})
    with pytest.raises(SettingsError):
        load_settings()


def test_refuses_nonpaper_endpoint(monkeypatch):
    apply(monkeypatch, {"ALPACA_TRADING_URL": "https://api.alpaca.markets/v2"})
    with pytest.raises(SettingsError):
        load_settings()


def test_accepts_exact_paper_endpoint(monkeypatch):
    apply(monkeypatch, {"ALPACA_TRADING_URL": PAPER_TRADING_URL})
    assert load_settings().broker_env == "paper"


def test_blank_required_secret_fails_closed(monkeypatch):
    apply(monkeypatch, {"ALPACA_PAPER_SECRET_KEY": ""})
    with pytest.raises(SettingsError):
        load_settings()


def test_blank_optional_controls_stay_safe(monkeypatch):
    apply(monkeypatch, {
        "PETRA_DRY_RUN": "",
        "PETRA_ENTRIES_ENABLED": "",
        "TICK_MAX_CANDIDATES": "",
        "ALPACA_OPTIONS_FEED": "",
    })
    s = load_settings()
    assert s.dry_run is True
    assert s.entries_enabled is False
    assert s.max_candidates == 1
    assert s.options_feed == "indicative"


def test_requires_paper_account_shape(monkeypatch):
    apply(monkeypatch, {"ALPACA_EXPECTED_ACCOUNT_NUMBER": "LIVE123"})
    with pytest.raises(SettingsError):
        load_settings()

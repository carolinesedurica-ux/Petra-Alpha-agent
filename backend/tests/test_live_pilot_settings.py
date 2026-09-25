import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from live_pilot_settings import LIVE_TRADING_URL, LivePilotSettingsError, load_live_pilot_settings


BASE = {
    "ALPACA_LIVE_API_KEY": "live-key",
    "ALPACA_LIVE_SECRET_KEY": "live-secret",
    "ALPACA_LIVE_EXPECTED_ACCOUNT_NUMBER": "LIVE_TEST_ACCOUNT",
    "PETRA_LIVE_MONGO_URL": "mongodb://example.invalid:27017",
    "PETRA_LIVE_DB_NAME": "petra_live_test",
    "ALLOW_LIVE_TRADING": "false",
}


def apply(monkeypatch, extra=None):
    for key in list(os.environ):
        if key.startswith("PETRA_LIVE_") or key.startswith("ALPACA_LIVE_") or key == "ALLOW_LIVE_TRADING":
            monkeypatch.delenv(key, raising=False)
    for key, value in BASE.items():
        monkeypatch.setenv(key, value)
    for key, value in (extra or {}).items():
        monkeypatch.setenv(key, value)


def test_defaults_are_shadow_and_small(monkeypatch):
    apply(monkeypatch)
    s = load_live_pilot_settings()
    assert s.dry_run is True
    assert s.armed is False
    assert s.can_submit is False
    assert s.trade_notional_usd == 5.0
    assert s.symbols == ("SPY", "QQQ")


def test_live_submission_requires_two_gates(monkeypatch):
    apply(monkeypatch, {"PETRA_LIVE_DRY_RUN": "false", "PETRA_LIVE_PILOT_ARMED": "true"})
    with pytest.raises(LivePilotSettingsError):
        load_live_pilot_settings()

    apply(monkeypatch, {
        "PETRA_LIVE_DRY_RUN": "false",
        "PETRA_LIVE_PILOT_ARMED": "true",
        "PETRA_LIVE_EXECUTION_CONFIRM": "LIVE_PILOT",
    })
    assert load_live_pilot_settings().can_submit is True


def test_generic_live_switch_must_stay_off(monkeypatch):
    apply(monkeypatch, {"ALLOW_LIVE_TRADING": "true"})
    with pytest.raises(LivePilotSettingsError):
        load_live_pilot_settings()


def test_refuses_paper_or_other_endpoint(monkeypatch):
    apply(monkeypatch, {"ALPACA_LIVE_TRADING_URL": "https://paper-api.alpaca.markets/v2"})
    with pytest.raises(LivePilotSettingsError):
        load_live_pilot_settings()


def test_accepts_exact_live_endpoint(monkeypatch):
    apply(monkeypatch, {"ALPACA_LIVE_TRADING_URL": LIVE_TRADING_URL})
    assert load_live_pilot_settings().dry_run is True


def test_trade_notional_capped_at_ten_dollars(monkeypatch):
    apply(monkeypatch, {"PETRA_LIVE_TRADE_NOTIONAL_USD": "11"})
    with pytest.raises(LivePilotSettingsError):
        load_live_pilot_settings()


def test_symbol_allowlist_is_tight(monkeypatch):
    apply(monkeypatch, {"PETRA_LIVE_SYMBOLS": "AAPL"})
    with pytest.raises(LivePilotSettingsError):
        load_live_pilot_settings()

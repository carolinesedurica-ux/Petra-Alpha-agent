"""Config tests for the read-only cTrader history exporter (no network, no broker)."""
import importlib
import os
import unittest
from unittest import mock

try:
    import ctrader_history_export as H
    AVAILABLE = True
except Exception:  # twisted / ctrader-open-api not installed
    AVAILABLE = False

DAY_MS = 24 * 60 * 60 * 1000


def reload_with(**env):
    keys = ("PETRA_HISTORY_DAYS", "PETRA_HISTORY_END_DAYS_AGO", "PETRA_HISTORY_TIMEOUT_SECONDS",
            "PETRA_HISTORY_SYMBOL", "PETRA_HISTORY_PERIOD", "PETRA_HISTORY_LIST_SYMBOLS")
    clean = {k: v for k, v in os.environ.items() if k not in keys}
    with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
        return importlib.reload(H)


@unittest.skipUnless(AVAILABLE, "bridge dependencies (twisted, ctrader-open-api) not installed")
class HistoryExportConfigTest(unittest.TestCase):
    def tearDown(self):
        reload_with()  # restore defaults for other tests

    def test_defaults_unchanged(self):
        m = reload_with()
        self.assertEqual((m.DAYS, m.END_DAYS_AGO, m.TIMEOUT_SECONDS), (180, 0, 150))

    def test_three_years_allowed_and_capped(self):
        self.assertEqual(reload_with(PETRA_HISTORY_DAYS="1095").DAYS, 1095)
        self.assertEqual(reload_with(PETRA_HISTORY_DAYS="99999").DAYS, 6000)
        self.assertEqual(reload_with(PETRA_HISTORY_DAYS="10").DAYS, 90)

    def test_timeout_bounds(self):
        self.assertEqual(reload_with(PETRA_HISTORY_TIMEOUT_SECONDS="1800").TIMEOUT_SECONDS, 1800)
        self.assertEqual(reload_with(PETRA_HISTORY_TIMEOUT_SECONDS="5").TIMEOUT_SECONDS, 60)
        self.assertEqual(reload_with(PETRA_HISTORY_TIMEOUT_SECONDS="999999").TIMEOUT_SECONDS, 3600)

    def test_window_math_with_offset(self):
        m = reload_with(PETRA_HISTORY_DAYS="365", PETRA_HISTORY_END_DAYS_AGO="365")
        with mock.patch.object(m.time, "time", return_value=2_000_000_000):
            st = m.State()
        self.assertEqual(st.window_end_ms, 2_000_000_000_000 - 365 * DAY_MS)
        self.assertEqual(st.start_ms, st.window_end_ms - 365 * DAY_MS)

    def test_default_window_ends_now(self):
        m = reload_with()
        with mock.patch.object(m.time, "time", return_value=2_000_000_000):
            st = m.State()
        self.assertEqual(st.window_end_ms, 2_000_000_000_000)
        self.assertEqual(st.start_ms, st.window_end_ms - 180 * DAY_MS)

    def test_defaults_are_us500_m5_and_not_listing(self):
        m = reload_with()
        self.assertEqual((m.TARGET_SYMBOL, m.PERIOD, m.WINDOW_DAYS, m.LIST_SYMBOLS), ("US500", "M5", 7, False))

    def test_other_symbol_period_and_listing_flags(self):
        m = reload_with(PETRA_HISTORY_SYMBOL="XAUUSD", PETRA_HISTORY_PERIOD="h1", PETRA_HISTORY_LIST_SYMBOLS="1")
        self.assertEqual((m.TARGET_SYMBOL, m.PERIOD, m.WINDOW_DAYS, m.LIST_SYMBOLS), ("XAUUSD", "H1", 100, True))

    def test_every_period_is_a_real_enum_and_a_window_fits_one_request(self):
        from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod
        bars_per_calendar_day = {"M5": 288, "M15": 96, "M30": 48, "H1": 24, "H4": 6, "D1": 1}
        m = reload_with()
        self.assertEqual(set(m.WINDOW_DAYS_BY_PERIOD), set(bars_per_calendar_day))
        for period, days in m.WINDOW_DAYS_BY_PERIOD.items():
            ProtoOATrendbarPeriod.Value(period)  # raises ValueError if the name is wrong
            self.assertLessEqual(days * bars_per_calendar_day[period], 2500, period)

    def test_invalid_period_is_rejected_before_any_connection(self):
        import types
        m = reload_with(PETRA_HISTORY_PERIOD="M7")
        stub = types.SimpleNamespace(environment="demo", dry_run=True, armed=False, can_submit=False)
        with mock.patch.object(m, "load_ctrader_settings", return_value=stub), \
                mock.patch.object(m, "Client", side_effect=AssertionError("must not connect")):
            self.assertEqual(m.main(), 2)

    def test_refuses_to_run_outside_demo_dry_run(self):
        src = open(H.__file__).read()
        self.assertIn('settings.environment!="demo"', src)
        for forbidden in ("ProtoOANewOrderReq", "ProtoOAClosePositionReq", "ProtoOAAmendOrderReq"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()

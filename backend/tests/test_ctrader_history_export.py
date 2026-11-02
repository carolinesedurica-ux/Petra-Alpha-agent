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
    keys = ("PETRA_HISTORY_DAYS", "PETRA_HISTORY_END_DAYS_AGO", "PETRA_HISTORY_TIMEOUT_SECONDS")
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
        self.assertEqual(reload_with(PETRA_HISTORY_DAYS="99999").DAYS, 1200)
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

    def test_refuses_to_run_outside_demo_dry_run(self):
        src = open(H.__file__).read()
        self.assertIn('settings.environment!="demo"', src)
        for forbidden in ("ProtoOANewOrderReq", "ProtoOAClosePositionReq", "ProtoOAAmendOrderReq"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

from ctrader_settings import CTraderSettingsError, load_ctrader_settings


BASE_ENV = {
    "ALLOW_LIVE_TRADING": "false",
    "PETRA_CTRADER_ENV": "demo",
    "CTRADER_CLIENT_ID": "client",
    "CTRADER_CLIENT_SECRET": "secret",
    "CTRADER_ACCESS_TOKEN": "token",
    "CTRADER_EXPECTED_ACCOUNT_ID": "123456",
    "PETRA_CTRADER_DRY_RUN": "true",
    "PETRA_CTRADER_PILOT_ARMED": "false",
    "PETRA_CTRADER_EXECUTION_CONFIRM": "",
}


class CTraderSettingsTests(unittest.TestCase):
    def load(self, **overrides):
        env = {**BASE_ENV, **overrides}
        with patch.dict(os.environ, env, clear=True):
            return load_ctrader_settings()

    def test_demo_defaults_fail_closed(self):
        settings = self.load()
        self.assertEqual(settings.environment, "demo")
        self.assertTrue(settings.dry_run)
        self.assertFalse(settings.can_submit)
        self.assertEqual(settings.expected_account_id, 123456)

    def test_live_read_only_is_allowed(self):
        settings = self.load(PETRA_CTRADER_ENV="live")
        self.assertTrue(settings.is_live_endpoint)
        self.assertFalse(settings.can_submit)

    def test_live_submission_requires_two_key_gate(self):
        with self.assertRaises(CTraderSettingsError):
            self.load(PETRA_CTRADER_ENV="live", PETRA_CTRADER_DRY_RUN="false")

        settings = self.load(
            PETRA_CTRADER_ENV="live",
            PETRA_CTRADER_DRY_RUN="false",
            PETRA_CTRADER_PILOT_ARMED="true",
            PETRA_CTRADER_EXECUTION_CONFIRM="CTRADER_LIVE_PILOT",
        )
        self.assertTrue(settings.can_submit)

    def test_generic_live_switch_must_remain_off(self):
        with self.assertRaises(CTraderSettingsError):
            self.load(ALLOW_LIVE_TRADING="true")

    def test_account_id_must_be_positive_integer(self):
        for bad in ("", "abc", "0", "-10"):
            with self.subTest(bad=bad):
                with self.assertRaises(CTraderSettingsError):
                    self.load(CTRADER_EXPECTED_ACCOUNT_ID=bad)


if __name__ == "__main__":
    unittest.main()

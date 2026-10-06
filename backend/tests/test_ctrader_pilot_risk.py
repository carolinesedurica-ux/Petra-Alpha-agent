import unittest

from ctrader_pilot_risk import PilotRiskPolicy, assess_candidate


class CTraderPilotRiskTests(unittest.TestCase):
    def test_small_minimum_margin_can_pass(self):
        result = assess_candidate(
            balance_usd=20,
            buy_margin_usd=4.2,
            sell_margin_usd=4.4,
            open_positions=0,
            pending_orders=0,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.margin_limit_usd, 5.0)
        self.assertEqual(result.conservative_margin_usd, 4.4)

    def test_margin_over_limit_fails_closed(self):
        result = assess_candidate(
            balance_usd=20,
            buy_margin_usd=5.01,
            sell_margin_usd=4.0,
            open_positions=0,
            pending_orders=0,
        )
        self.assertFalse(result.eligible)
        self.assertIn("exceeds", result.reason)

    def test_existing_position_or_order_blocks_new_entry(self):
        for positions, orders in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(positions=positions, orders=orders):
                result = assess_candidate(
                    balance_usd=20,
                    buy_margin_usd=1,
                    sell_margin_usd=1,
                    open_positions=positions,
                    pending_orders=orders,
                )
                self.assertFalse(result.eligible)

    def test_daily_loss_stop_blocks_new_entry(self):
        result = assess_candidate(
            balance_usd=20,
            buy_margin_usd=1,
            sell_margin_usd=1,
            open_positions=0,
            pending_orders=0,
            realized_daily_pnl_usd=-1.0,
        )
        self.assertFalse(result.eligible)
        self.assertIn("daily loss", result.reason)

    def test_smaller_balance_reduces_margin_limit(self):
        result = assess_candidate(
            balance_usd=10,
            buy_margin_usd=2.4,
            sell_margin_usd=2.4,
            open_positions=0,
            pending_orders=0,
        )
        self.assertTrue(result.eligible)
        self.assertEqual(result.margin_limit_usd, 2.5)


if __name__ == "__main__":
    unittest.main()

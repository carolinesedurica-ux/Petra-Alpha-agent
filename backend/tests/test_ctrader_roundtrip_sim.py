import unittest

from ctrader_roundtrip_sim import RoundTripState


class RoundTripSimTests(unittest.TestCase):
    def test_happy_path_completes_flat(self):
        s = RoundTripState()
        s.preflight(open_positions=0, pending_orders=0)
        s.risk_gate(buy_margin_usd=3.91, sell_margin_usd=3.91)
        s.record_fill(
            position_id=11,
            order_id=22,
            symbol="US500",
            requested_volume_cents=10,
            filled_volume_cents=10,
            entry_price=6700.0,
            stop_loss=6690.0,
        )
        s.request_close(position_id=11, volume_cents=10)
        s.record_close(position_id=11)
        s.final_reconcile(open_positions=0, pending_orders=0)
        self.assertEqual(s.stage, "complete")
        self.assertTrue(s.closed)

    def test_rejects_non_flat_preflight(self):
        s = RoundTripState()
        with self.assertRaises(RuntimeError):
            s.preflight(open_positions=1, pending_orders=0)

    def test_rejects_missing_stop(self):
        s = RoundTripState()
        s.preflight(open_positions=0, pending_orders=0)
        s.risk_gate(buy_margin_usd=3.91, sell_margin_usd=3.91)
        with self.assertRaises(RuntimeError):
            s.record_fill(
                position_id=11,
                order_id=22,
                symbol="US500",
                requested_volume_cents=10,
                filled_volume_cents=10,
                entry_price=6700.0,
                stop_loss=None,
            )

    def test_rejects_over_margin_candidate(self):
        s = RoundTripState()
        s.preflight(open_positions=0, pending_orders=0)
        with self.assertRaises(RuntimeError):
            s.risk_gate(buy_margin_usd=5.63, sell_margin_usd=5.63)

    def test_rejects_volume_mismatch(self):
        s = RoundTripState()
        s.preflight(open_positions=0, pending_orders=0)
        s.risk_gate(buy_margin_usd=3.91, sell_margin_usd=3.91)
        with self.assertRaises(RuntimeError):
            s.record_fill(
                position_id=11,
                order_id=22,
                symbol="US500",
                requested_volume_cents=10,
                filled_volume_cents=20,
                entry_price=6700.0,
                stop_loss=6690.0,
            )

    def test_kill_switch_blocks_progress(self):
        s = RoundTripState()
        s.trip_kill_switch()
        with self.assertRaises(RuntimeError):
            s.preflight(open_positions=0, pending_orders=0)

    def test_final_reconcile_must_be_flat(self):
        s = RoundTripState()
        s.preflight(open_positions=0, pending_orders=0)
        s.risk_gate(buy_margin_usd=3.91, sell_margin_usd=3.91)
        s.record_fill(
            position_id=11,
            order_id=22,
            symbol="US500",
            requested_volume_cents=10,
            filled_volume_cents=10,
            entry_price=6700.0,
            stop_loss=6690.0,
        )
        s.request_close(position_id=11, volume_cents=10)
        s.record_close(position_id=11)
        with self.assertRaises(RuntimeError):
            s.final_reconcile(open_positions=1, pending_orders=0)


if __name__ == "__main__":
    unittest.main()

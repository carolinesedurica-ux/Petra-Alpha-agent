"""Mechanics tests for the cooldown/flip backtest harness (synthetic data; says nothing about edge)."""
import unittest
import warnings

warnings.filterwarnings("ignore")
try:
    import backtest_paper as B
    import ctrader_autonomous_bridge  # noqa: F401  (needs twisted + ctrader-open-api)
    AVAILABLE = True
except Exception:  # twisted / ctrader_open_api not installed in this environment
    AVAILABLE = False

BAR = 300


@unittest.skipUnless(AVAILABLE, "bridge dependencies (twisted, ctrader-open-api) not installed")
class BacktestMechanicsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = B.synthetic_bars(2200, seed=3)
        cls.base = B.run_variant(cls.bars, B.Variant())

    def test_baseline_is_deterministic_and_nonempty(self):
        again = B.run_variant(self.bars, B.Variant("other-name"))
        self.assertGreater(len(self.base), 20)
        self.assertEqual([(t.opened_ts, t.exit, t.exit_reason) for t in self.base],
                         [(t.opened_ts, t.exit, t.exit_reason) for t in again])

    def test_baseline_reverses_immediately_after_flip_closes(self):
        flips = [t for t in self.base if t.exit_reason == "OPPOSITE_SIGNAL"]
        self.assertTrue(flips)
        starts = {t.opened_ts for t in self.base}
        self.assertTrue(any(t.closed_ts in starts for t in flips),
                        "baseline should reverse in the same cycle as an opposite-signal close")

    def test_cooldown_enforces_gap_after_any_close(self):
        n = 6
        trades = B.run_variant(self.bars, B.Variant(cooldown_bars=n))
        for prev, nxt in zip(trades, trades[1:]):
            self.assertGreaterEqual(nxt.opened_ts - prev.closed_ts, n * BAR)
        self.assertLess(len(trades), len(self.base))

    def test_no_reverse_blocks_entry_after_opposite_close_only(self):
        n = 6
        trades = B.run_variant(self.bars, B.Variant(flip_cooldown_bars=n))
        flips_seen = 0
        for prev, nxt in zip(trades, trades[1:]):
            if prev.exit_reason == "OPPOSITE_SIGNAL":
                flips_seen += 1
                self.assertGreaterEqual(nxt.opened_ts - prev.closed_ts, n * BAR)
        self.assertGreater(flips_seen, 0)

    def test_min_hold_delays_opposite_closes(self):
        n = 6
        trades = B.run_variant(self.bars, B.Variant(min_hold_bars=n))
        for t in trades:
            if t.exit_reason == "OPPOSITE_SIGNAL":
                self.assertGreaterEqual(t.bars_held, n)
        # stops/targets remain active during the hold
        self.assertTrue(any(t.bars_held < n for t in trades if t.exit_reason in {"STOP_LOSS", "TAKE_PROFIT"}))

    def test_stricter_flip_confidence_never_adds_flip_exits(self):
        strict = B.run_variant(self.bars, B.Variant(flip_conf_extra=0.2))
        self.assertLessEqual(sum(t.exit_reason == "OPPOSITE_SIGNAL" for t in strict),
                             sum(t.exit_reason == "OPPOSITE_SIGNAL" for t in self.base))

    def test_spread_cost_reduces_points_per_trade(self):
        costly = B.run_variant(self.bars, B.Variant(), spread_pts=0.5)
        self.assertEqual(len(costly), len(self.base))
        for a, b in zip(self.base, costly):
            self.assertAlmostEqual(a.points - b.points, 0.5, places=6)

    def test_metrics_math(self):
        def tr(p):
            return B.Trade("BUY", 100, 100 + p, "X", 1, 1.0, p, p)
        m = B.metrics([tr(2), tr(-1), tr(3), tr(-1), tr(-1)], n_bars=B.WARMUP + 288)
        self.assertEqual(m["trades"], 5)
        self.assertAlmostEqual(m["win_rate"], 0.4)
        self.assertAlmostEqual(m["profit_factor"], 5 / 3)
        self.assertAlmostEqual(m["total_pts"], 2.0)
        self.assertAlmostEqual(m["max_dd_pts"], 2.0)
        self.assertAlmostEqual(m["trades_per_day"], 5.0)
        self.assertEqual(B.metrics([], 500)["trades"], 0)


if __name__ == "__main__":
    unittest.main()

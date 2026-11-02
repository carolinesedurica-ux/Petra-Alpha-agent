"""Mechanics tests for the cooldown/flip backtest harness (synthetic data; says nothing about edge)."""
import unittest
import warnings

warnings.filterwarnings("ignore")
import backtest_paper as B  # lazy bridge import: safe without twisted / ctrader-open-api

try:
    import ctrader_autonomous_bridge  # noqa: F401  (needs twisted + ctrader-open-api)
    AVAILABLE = True
except Exception:  # not installed in this environment
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


def _analysis(decision="BUY", price=7000.0, stop=6994.0, tp=7009.6, trend=0.5, atr=4.0):
    return {"decision": decision, "confidence": 0.8, "price": price, "suggested_stop_loss": stop,
            "suggested_take_profit": tp, "trend_strength": trend, "atr_14": atr}


class SignalFilterTest(unittest.TestCase):
    TS_US = 1_800_000_000 - (1_800_000_000 % 86400) + 15 * 3600   # 15:00 UTC
    TS_NIGHT = 1_800_000_000 - (1_800_000_000 % 86400) + 3 * 3600  # 03:00 UTC

    def f(self, a, **kw):
        ts = kw.pop("ts", self.TS_US)
        closes = kw.pop("closes", None)
        return B.apply_signal_filters(a, B.Variant(**kw), closes_long=closes, bar_close_ts=ts)

    def test_default_variant_is_identity(self):
        a = _analysis()
        self.assertEqual(self.f(a), a)

    def test_no_trade_passes_through(self):
        a = _analysis("NO_TRADE", stop=None, tp=None)
        self.assertEqual(self.f(a, min_atr=99)["decision"], "NO_TRADE")

    def test_trend_strength_and_atr_floors_block_weak_setups(self):
        self.assertEqual(self.f(_analysis(trend=0.3), min_trend_strength=0.4)["decision"], "NO_TRADE")
        self.assertEqual(self.f(_analysis(trend=0.5), min_trend_strength=0.4)["decision"], "BUY")
        self.assertEqual(self.f(_analysis(atr=2.0), min_atr=3.0)["decision"], "NO_TRADE")
        self.assertEqual(self.f(_analysis(atr=3.0), min_atr=3.0)["decision"], "BUY")

    def test_hours_filter(self):
        self.assertEqual(self.f(_analysis(), hours_utc=(13, 20))["decision"], "BUY")
        self.assertEqual(self.f(_analysis(), hours_utc=(13, 20), ts=self.TS_NIGHT)["decision"], "NO_TRADE")
        self.assertEqual(self.f(_analysis(), hours_utc=(13, 15))["decision"], "NO_TRADE")  # end exclusive

    def test_htf_filter_requires_agreement_and_enough_history(self):
        up = [100.0 + i * 0.1 for i in range(250)]        # price above its long EMA
        down = list(reversed(up))                          # price below its long EMA
        self.assertEqual(self.f(_analysis("BUY"), htf_ema_period=200, closes=up)["decision"], "BUY")
        self.assertEqual(self.f(_analysis("BUY"), htf_ema_period=200, closes=down)["decision"], "NO_TRADE")
        self.assertEqual(self.f(_analysis("SELL"), htf_ema_period=200, closes=down)["decision"], "SELL")
        self.assertEqual(self.f(_analysis("SELL"), htf_ema_period=200, closes=up)["decision"], "NO_TRADE")
        self.assertEqual(self.f(_analysis("BUY"), htf_ema_period=200, closes=up[:50])["decision"], "NO_TRADE")
        self.assertEqual(self.f(_analysis("BUY"), htf_ema_period=200, closes=None)["decision"], "NO_TRADE")

    def test_stop_and_rr_geometry_both_sides(self):
        b = self.f(_analysis("BUY", stop=6994.0), stop_mult=2.0, rr=2.5)   # stop dist 6 -> 12, target 30
        self.assertEqual((b["suggested_stop_loss"], b["suggested_take_profit"]), (6988.0, 7030.0))
        s = self.f(_analysis("SELL", stop=7006.0, tp=6990.4), stop_mult=1.5)  # stop 9, target 1.6*9
        self.assertEqual((s["suggested_stop_loss"], s["suggested_take_profit"]), (7009.0, 6985.6))

    def test_input_not_mutated(self):
        a = _analysis()
        before = dict(a)
        self.f(a, min_atr=99, stop_mult=2.0)
        self.assertEqual(a, before)


if __name__ == "__main__":
    unittest.main()

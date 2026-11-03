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

    def test_wider_ci_level_gives_wider_interval(self):
        def tr(p):
            return B.Trade("BUY", 100, 100 + p, "X", 1, 1.0, p, p, 0, 0, 5.0)
        trades = [tr(p) for p in ([3, -2, 4, -1, 2, -3, 5, -2, 1, -1] * 20)]
        m95 = B.metrics(trades, 5000)
        m99 = B.metrics(trades, 5000, ci_level=0.99, resamples=2000)
        self.assertLess(m99["ci_lo"], m95["ci_lo"])
        self.assertGreater(m99["ci_hi"], m95["ci_hi"])
        self.assertEqual(m99["ci_level"], 0.99)
        self.assertTrue(m99["ci_lo"] <= m99["expectancy_pts"] <= m99["ci_hi"])

    def test_period_breakdown_partitions_by_open_time(self):
        def tr(opened, p):
            return B.Trade("BUY", 100, 100 + p, "X", 1, 1.0, p, p, opened, opened + 300, 5.0)
        trades = [tr(10, 5.0), tr(20, -1.0), tr(110, 2.0), tr(250, -4.0)]
        rows = B.period_breakdown(trades, [("a", 0, 100), ("b", 100, 200), ("c", 200, 300)])
        self.assertEqual([r["trades"] for r in rows], [2, 1, 1])
        self.assertAlmostEqual(rows[0]["expectancy_pts"], 2.0)
        self.assertAlmostEqual(rows[0]["expectancy_r"], 0.4)
        self.assertAlmostEqual(rows[2]["expectancy_r"], -0.8)
        self.assertEqual(B.period_breakdown([], [("x", 0, 1)])[0]["trades"], 0)

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


class LoadBarsTest(unittest.TestCase):
    def write(self, d, name, rows):
        path = f"{d}/{name}"
        with open(path, "w") as fh:
            fh.write("time,open,high,low,close\n")
            for t, c in rows:
                fh.write(f"{t},{c},{c + 1},{c - 1},{c}\n")
        return path

    def test_merges_slices_dedupes_overlap_and_sorts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            newer = self.write(d, "newer.csv", [("2026-02-01T00:10:00Z", 12), ("2026-02-01T00:05:00Z", 11)])
            older = self.write(d, "older.csv", [("2026-02-01T00:00:00Z", 10), ("2026-02-01T00:05:00Z", 99)])
            bars = B.load_bars([newer, older])
            self.assertEqual(B.load_bars(newer)[0]["close"], 11)  # sorted even for a single unsorted file
        self.assertEqual([b["close"] for b in bars], [10, 99, 12])  # on a duplicate timestamp the later file wins
        self.assertEqual([b["time"] for b in bars], sorted(b["time"] for b in bars))

    def test_missing_columns_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bad = f"{d}/bad.csv"
            with open(bad, "w") as fh:
                fh.write("time,open,close\n2026-02-01T00:00:00Z,1,2\n")
            with self.assertRaises(SystemExit):
                B.load_bars(bad)


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

from datetime import datetime as _dt
from zoneinfo import ZoneInfo as _Z


def ts_et(y, m, d, hh, mm):
    return int(_dt(y, m, d, hh, mm, tzinfo=_Z("America/New_York")).timestamp())


def day_bars(y, m, d, closes_by_slot=None, start=(9, 0), end=(16, 5), hi=7010.0, lo=7000.0):
    """5-min bars for one ET day. Bars opening 09:30-09:40 form a 7000-7010 range."""
    out, t = [], ts_et(y, m, d, *start)
    stop = ts_et(y, m, d, *end)
    overrides = closes_by_slot or {}
    while t < stop:
        et = _dt.fromtimestamp(t, _Z("America/New_York"))
        key = (et.hour, et.minute)
        close = overrides.get(key, 7005.0)
        h = max(close, hi) if (9, 30) <= key < (9, 45) else max(close, 7006.0)
        l = min(close, lo) if (9, 30) <= key < (9, 45) else min(close, 7004.0)
        out.append({"time": t, "open": 7005.0, "high": h, "low": l, "close": close})
        t += 300
    return out


class OrbSignalTest(unittest.TestCase):
    DAYS = [(2026, 7, 15), (2026, 12, 15)]  # EDT and EST: US open is 13:30 vs 14:30 UTC

    def idx(self, bars, hh, mm, y, m, d):
        t = ts_et(y, m, d, hh, mm)
        return next(i for i, b in enumerate(bars) if b["time"] == t)

    def test_buy_breakout_after_range_with_range_stop_both_sides_of_dst(self):
        for y, m, d in self.DAYS:
            bars = day_bars(y, m, d, {(9, 45): 7012.0})
            i = self.idx(bars, 9, 45, y, m, d)
            sig = B.orb_signal(bars, i, 15, 1.6)
            self.assertEqual(sig["decision"], "BUY", (y, m, d))
            self.assertEqual(sig["suggested_stop_loss"], 7000.0)  # opposite side of the range
            self.assertEqual(sig["suggested_take_profit"], round(7012.0 + 12.0 * 1.6, 5))

    def test_sell_breakout_uses_range_high_as_stop(self):
        y, m, d = self.DAYS[0]
        bars = day_bars(y, m, d, {(9, 50): 6995.0})
        sig = B.orb_signal(bars, self.idx(bars, 9, 50, y, m, d), 15, 1.0)
        self.assertEqual(sig["decision"], "SELL")
        self.assertEqual(sig["suggested_stop_loss"], 7010.0)  # opposite side of the range
        self.assertEqual(sig["suggested_take_profit"], round(6995.0 - 15.0, 5))

    def test_bar_completing_the_range_cannot_break_it(self):
        y, m, d = self.DAYS[0]
        bars = day_bars(y, m, d, {(9, 40): 7020.0})  # closes at 09:45 = range end
        self.assertEqual(B.orb_signal(bars, self.idx(bars, 9, 40, y, m, d), 15, 1.6)["decision"], "NO_TRADE")

    def test_no_signal_inside_range_before_open_after_cutoff_or_when_already_traded(self):
        y, m, d = self.DAYS[0]
        bars = day_bars(y, m, d, {(9, 50): 7008.0, (11, 30): 7050.0, (9, 55): 7050.0})
        self.assertEqual(B.orb_signal(bars, self.idx(bars, 9, 50, y, m, d), 15, 1.6)["decision"], "NO_TRADE")
        self.assertEqual(B.orb_signal(bars, self.idx(bars, 9, 0, y, m, d), 15, 1.6)["decision"], "NO_TRADE")
        self.assertEqual(B.orb_signal(bars, self.idx(bars, 11, 30, y, m, d), 15, 1.6)["decision"], "NO_TRADE")
        i = self.idx(bars, 9, 55, y, m, d)
        self.assertEqual(B.orb_signal(bars, i, 15, 1.6)["decision"], "BUY")
        self.assertEqual(B.orb_signal(bars, i, 15, 1.6, traded_day=B.et_parts(bars[i]["time"] + 300)[0])["decision"],
                         "NO_TRADE")

    def test_incomplete_range_is_skipped(self):
        y, m, d = self.DAYS[0]
        bars = [b for b in day_bars(y, m, d, {(9, 50): 7050.0})
                if B.et_parts(b["time"])[1] != 9 * 60 + 35]  # drop one range bar
        i = next(k for k, b in enumerate(bars) if B.et_parts(b["time"])[1] == 9 * 60 + 50)
        self.assertEqual(B.orb_signal(bars, i, 15, 1.6)["decision"], "NO_TRADE")

    def test_minimum_stop_floor_applies(self):
        y, m, d = self.DAYS[0]
        bars = day_bars(y, m, d, {(9, 45): 7010.5}, hi=7010.0, lo=7009.0)
        for b in bars:
            if B.et_parts(b["time"])[1] in (9 * 60 + 30, 9 * 60 + 35, 9 * 60 + 40):
                b.update(high=7010.0, low=7009.0, close=7009.5)
        sig = B.orb_signal(bars, self.idx(bars, 9, 45, y, m, d), 15, 1.0)
        self.assertEqual(sig["decision"], "BUY")
        self.assertAlmostEqual(7010.5 - sig["suggested_stop_loss"], 7010.5 * B.MIN_STOP_FRAC, places=3)


class DonchianSignalTest(unittest.TestCase):
    def bars(self, n=80, last_close=7000.0):
        out = [{"time": 1_800_000_000 + k * 300, "open": 7000.0, "high": 7001.0, "low": 6999.0, "close": 7000.0}
               for k in range(n)]
        out[-1].update(close=last_close, high=max(last_close, 7001.0), low=min(last_close, 6999.0))
        return out

    def test_breakout_up_down_and_equal_is_not_a_break(self):
        self.assertEqual(B.donchian_signal(self.bars(last_close=7002.0), 79, 24)["decision"], "BUY")
        self.assertEqual(B.donchian_signal(self.bars(last_close=6998.0), 79, 24)["decision"], "SELL")
        self.assertEqual(B.donchian_signal(self.bars(last_close=7001.0), 79, 24)["decision"], "NO_TRADE")
        self.assertEqual(B.donchian_signal(self.bars(last_close=7000.0), 79, 24)["decision"], "NO_TRADE")

    def test_warmup_and_geometry(self):
        self.assertEqual(B.donchian_signal(self.bars(n=30, last_close=7002.0), 29, 24)["decision"], "NO_TRADE")
        sig = B.donchian_signal(self.bars(last_close=7002.0), 79, 24)
        stop_dist = 7002.0 - sig["suggested_stop_loss"]
        self.assertGreaterEqual(stop_dist, 7002.0 * B.MIN_STOP_FRAC - 1e-6)
        self.assertAlmostEqual(sig["suggested_take_profit"] - 7002.0, stop_dist * 1.6, places=3)

    def test_us_cash_hours_filter(self):
        bars = self.bars(last_close=7002.0)
        inside = ts_et(2026, 7, 15, 10, 0)
        outside = ts_et(2026, 7, 15, 3, 0)
        self.assertEqual(B.donchian_signal(bars, 79, 24, et_hours_only=True, bar_close_ts=inside)["decision"], "BUY")
        self.assertEqual(B.donchian_signal(bars, 79, 24, et_hours_only=True, bar_close_ts=outside)["decision"],
                         "NO_TRADE")


@unittest.skipUnless(AVAILABLE, "bridge dependencies (twisted, ctrader-open-api) not installed")
class BreakoutIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = B.synthetic_bars(3300, seed=5)
        cls.orb = B.run_variant(cls.bars, B.Variant(signal="orb", or_minutes=15, orb_rr=1.6))
        cls.don = B.run_variant(cls.bars, B.Variant(signal="donchian", n_lookback=24))

    def test_orb_one_trade_per_day_inside_window_and_flat_same_day(self):
        self.assertGreater(len(self.orb), 3)
        days = [B.et_parts(t.opened_ts)[0] for t in self.orb]
        self.assertEqual(len(days), len(set(days)), "more than one ORB trade in a day")
        for t in self.orb:
            d_open, m_open = B.et_parts(t.opened_ts)
            d_close, m_close = B.et_parts(t.closed_ts)
            self.assertTrue(9 * 60 + 30 + 15 <= m_open <= 11 * 60 + 35, m_open)
            self.assertEqual(d_open, d_close, "ORB position held overnight")
            self.assertNotEqual(t.exit_reason, "OPPOSITE_SIGNAL")
            self.assertIn(t.exit_reason, {"STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT"})

    def test_donchian_trades_and_respects_geometry(self):
        self.assertGreater(len(self.don), 20)
        for t in self.don:
            self.assertGreater(t.risk_pts, 0)



if __name__ == "__main__":
    unittest.main()

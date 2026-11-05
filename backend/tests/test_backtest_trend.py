"""Tests for the daily trend-following backtest (stdlib only; synthetic data, says nothing about edge)."""
import math
import os
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone

import backtest_trend as T

DAY = 86400
MON = int(datetime(2015, 1, 5, tzinfo=timezone.utc).timestamp())  # a Monday


def weekday_ts(n):
    out, t = [], MON
    while len(out) < n:
        if datetime.fromtimestamp(t, timezone.utc).weekday() < 5:
            out.append(t)
        t += DAY
    return out


def make_series(n=900, drift=0.0015, noise=0.006, seed=1, start=100.0):
    rng = random.Random(seed)
    price, out = start, []
    for t in weekday_ts(n):
        price *= math.exp(drift + rng.gauss(0, noise))
        out.append((t, price))
    return out


def rets_of(series):
    c = [x for _, x in series]
    return c, [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, len(c))]


class DataTest(unittest.TestCase):
    def test_load_daily_sorts_dedupes_and_drops_bad_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "X_d1.csv")
            with open(p, "w") as fh:
                fh.write("time,open,high,low,close\n2020-01-03T00:00:00Z,1,1,1,12\n2020-01-02T00:00:00Z,1,1,1,10\n"
                         "2020-01-02T00:00:00Z,1,1,1,11\n2020-01-04T00:00:00Z,1,1,1,0\n1578182400,1,1,1,13\n")
            rows = T.load_daily(p)
        self.assertEqual([c for _, c in rows], [11.0, 12.0, 13.0])  # later duplicate wins, zero close dropped
        self.assertEqual([t for t, _ in rows], sorted(t for t, _ in rows))

    def test_missing_columns_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad_d1.csv")
            with open(p, "w") as fh:
                fh.write("time,open\n2020-01-02T00:00:00Z,1\n")
            with self.assertRaises(SystemExit):
                T.load_daily(p)

    def test_flag_big_moves(self):
        s = [(1, 100.0), (2, 101.0), (3, 140.0), (4, 139.0)]
        self.assertEqual([t for t, _ in T.flag_big_moves(s)], [3])

    def test_split_boundary(self):
        basket = {"A": [(0, 1.0), (1000 * DAY, 1.0)], "B": [(100 * DAY, 1.0), (900 * DAY, 1.0)]}
        self.assertEqual(T.split_boundary(basket), 750 * DAY)


class StrategyMechanicsTest(unittest.TestCase):
    def test_no_lookahead_changing_the_future_cannot_change_the_past(self):
        a = make_series(700, seed=3)
        b = list(a)
        cut = 600
        b[cut:] = [(t, p * 1.7) for t, p in b[cut:]]  # change every close from index 600 on
        ra, rb = T.run_instrument("A", a, T.Spec()), T.run_instrument("B", b, T.Spec())
        t_cut = a[cut][0]
        for name in ("gross", "cost", "fin"):
            before_a = {k: v for k, v in getattr(ra, name).items() if k < t_cut}
            before_b = {k: v for k, v in getattr(rb, name).items() if k < t_cut}
            self.assertEqual(before_a, before_b, name)

    def test_positions_change_only_at_the_last_bar_of_each_week(self):
        s = make_series(900, seed=4)
        run = T.run_instrument("A", s, T.Spec())
        ts = [t for t, _ in s]
        self.assertTrue(run.cost)
        for d in run.cost:
            i = ts.index(d)
            self.assertTrue(i == len(ts) - 1 or T._week_key(ts[i + 1]) != T._week_key(ts[i]), d)

    def test_uptrend_long_downtrend_short_both_profit_gross(self):
        up = T.run_instrument("U", make_series(900, drift=0.002, noise=0.004, seed=5), T.Spec())
        down = T.run_instrument("D", make_series(900, drift=-0.002, noise=0.004, seed=6), T.Spec())
        self.assertGreater(sum(up.gross.values()), 0)
        self.assertGreater(sum(down.gross.values()), 0)

    def test_long_only_stays_flat_in_a_downtrend(self):
        down = make_series(900, drift=-0.002, noise=0.004, seed=6)
        run = T.run_instrument("D", down, replace(T.Spec(), long_only=True))
        self.assertEqual(sum(abs(v) for v in run.gross.values()), 0.0)
        self.assertEqual(run.turnover, 0.0)

    def test_weights_are_capped_at_max_leverage(self):
        s = make_series(400, drift=0.001, noise=1e-5, seed=7)  # almost no volatility
        c, r = rets_of(s)
        w = T._target_weight(c, r, 300, T.Spec())
        self.assertEqual(w, 3.0)
        self.assertEqual(T._target_weight(c, r, 300, replace(T.Spec(), max_leverage=1.5)), 1.5)

    def test_no_position_before_enough_history(self):
        s = make_series(600, seed=8)
        spec = T.Spec()
        run = T.run_instrument("A", s, spec)
        first_trade_ts = min(run.gross)
        self.assertGreaterEqual([t for t, _ in s].index(first_trade_ts), spec.min_history)
        c, r = rets_of(s)
        self.assertEqual(T._target_weight(c, r, spec.min_history - 2, spec), 0.0)

    def test_costs_zero_means_net_equals_gross_and_cost_matches_turnover(self):
        s = make_series(900, seed=9)
        free = T.run_portfolio({"A": s}, replace(T.Spec(), cost_bps=0.0, financing_annual=0.0))
        self.assertEqual(free.net, free.gross)
        paid = T.run_portfolio({"A": s}, T.Spec())
        self.assertLess(sum(paid.net), sum(paid.gross))
        run = T.run_instrument("A", s, T.Spec())
        self.assertAlmostEqual(sum(run.cost.values()), run.turnover * 5.0 / 1e4, places=12)
        d0 = min(run.cost)
        c, r = rets_of(s)
        t0 = [t for t, _ in s].index(d0)
        self.assertAlmostEqual(run.cost[d0], abs(T._target_weight(c, r, t0, T.Spec())) * 5e-4, places=12)

    def test_financing_accrues_per_calendar_day_including_weekends(self):
        s = make_series(900, drift=0.002, noise=0.004, seed=5)
        run = T.run_instrument("A", s, T.Spec())
        ts = [t for t, _ in s]
        checked = 0
        for i in range(2, len(ts) - 1):
            if ts[i] in run.fin and ts[i + 1] in run.fin:
                wd0, wd1 = datetime.fromtimestamp(ts[i], timezone.utc).weekday(), datetime.fromtimestamp(ts[i + 1], timezone.utc).weekday()
                if wd0 == 0 and wd1 == 1:  # Monday then Tuesday: same weekly position; Mon spans 3 days, Tue 1
                    self.assertAlmostEqual(run.fin[ts[i]] / run.fin[ts[i + 1]], 3.0, places=9)
                    checked += 1
        self.assertGreater(checked, 5)

    def test_identical_instruments_equal_weight_average(self):
        s = make_series(900, seed=10)
        one = T.run_portfolio({"A": s}, T.Spec())
        two = T.run_portfolio({"A": s, "B": list(s)}, T.Spec())
        self.assertEqual(one.dates, two.dates)
        for x, y in zip(one.net, two.net):
            self.assertAlmostEqual(x, y, places=12)

    def test_basket_with_later_starting_instrument_dilutes_only_once_active(self):
        long_s = make_series(900, seed=11)
        short_s = long_s[400:]
        both = T.run_portfolio({"L": long_s, "S": short_s}, T.Spec())
        alone = T.run_portfolio({"L": long_s}, T.Spec())
        first_s_active = both.runs["S"].first_active_ts
        self.assertIsNotNone(first_s_active)
        a = dict(zip(alone.dates, alone.net))
        b = dict(zip(both.dates, both.net))
        early = [d for d in b if d < first_s_active and d in a]
        self.assertTrue(early)
        for d in early:
            self.assertAlmostEqual(a[d], b[d], places=12)  # before S is active, N = 1


class TradingDayConventionTest(unittest.TestCase):
    """Broker daily bars are stamped at the session open (21:00/22:00 UTC the evening before)."""

    @staticmethod
    def broker_series(n=700, seed=31):
        """Same prices as make_series, but each weekday session is stamped 22:00 UTC the previous evening."""
        out = []
        for (t, c) in make_series(n, seed=seed):
            out.append((t - 2 * 3600, c))  # trading date D stamped at 22:00 on D-1
        return out

    def test_friday_session_is_stamped_thursday_night_and_maps_to_friday(self):
        thu_night = int(datetime(2020, 1, 9, 22, 0, tzinfo=timezone.utc).timestamp())  # Thursday 22:00 UTC
        self.assertEqual(T.trading_date(thu_night).weekday(), 4)  # Friday
        sun_night = int(datetime(2020, 1, 12, 22, 0, tzinfo=timezone.utc).timestamp())  # Sunday 22:00 UTC
        self.assertEqual(T.trading_date(sun_night).weekday(), 0)  # Monday
        for hour in (21, 22):
            ts = int(datetime(2020, 7, 1, hour, 0, tzinfo=timezone.utc).timestamp())  # Wed night -> Thursday
            self.assertEqual(T.trading_date(ts).weekday(), 3)

    def test_sunday_dated_session_belongs_to_the_following_week(self):
        sat_night = int(datetime(2012, 1, 14, 22, 0, tzinfo=timezone.utc).timestamp())  # old-style Saturday stamp
        mon = int(datetime(2012, 1, 16, 12, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(T._week_key(sat_night), T._week_key(mon))

    def test_rebalance_happens_at_the_friday_session_close(self):
        series = self.broker_series()
        run = T.run_instrument("A", series, T.Spec())
        self.assertTrue(run.cost)
        weekdays = {T.trading_date(d).weekday() for d in run.cost}
        self.assertEqual(weekdays, {4})  # every rebalance is on a Friday session

    def test_results_match_the_same_prices_with_midnight_stamps(self):
        a = T.run_instrument("A", self.broker_series(), T.Spec())
        b = T.run_instrument("A", make_series(700, seed=31), T.Spec())
        self.assertEqual(len(a.cost), len(b.cost))
        self.assertAlmostEqual(sum(a.gross.values()), sum(b.gross.values()), places=10)
        self.assertAlmostEqual(sum(a.cost.values()), sum(b.cost.values()), places=12)
        self.assertAlmostEqual(sum(a.fin.values()), sum(b.fin.values()), places=12)

    def test_raw_stamp_grouping_would_have_been_wrong(self):
        """Documents the bug this convention fixes: grouping by raw stamp puts the rebalance on Monday sessions."""
        series = self.broker_series()
        ts = [t for t, _ in series]
        raw_last = [t for i, t in enumerate(ts[:-1])
                    if datetime.fromtimestamp(ts[i + 1], timezone.utc).isocalendar()[:2] != datetime.fromtimestamp(t, timezone.utc).isocalendar()[:2]]
        self.assertEqual({T.trading_date(t).weekday() for t in raw_last}, {0})  # Monday: not what was intended


class StatsTest(unittest.TestCase):
    def test_sharpe_drawdown_summary(self):
        self.assertEqual(T.sharpe([0.0] * 10), 0.0)
        self.assertEqual(T.sharpe([0.01]), 0.0)
        self.assertAlmostEqual(T.max_drawdown([0.1, -0.5]), 0.5)
        self.assertEqual(T.max_drawdown([0.01, 0.01]), 0.0)
        s = T.summarize([0, 1], [0.01, 0.01])
        self.assertAlmostEqual(s["total"], 1.01 * 1.01 - 1)

    def test_bootstrap_properties(self):
        rng = random.Random(2)
        rets = [rng.gauss(0.0004, 0.01) for _ in range(800)]
        b1 = T.stationary_bootstrap_sharpe(rets, resamples=300, seed=11)
        b2 = T.stationary_bootstrap_sharpe(rets, resamples=300, seed=11)
        self.assertEqual(b1, b2)
        self.assertEqual(len(b1), 300)
        self.assertEqual(b1, sorted(b1))
        self.assertLess(b1[int(0.05 * 300)], T.sharpe(rets))
        self.assertGreater(b1[int(0.95 * 300)], T.sharpe(rets))
        self.assertEqual(set(T.stationary_bootstrap_sharpe([0.001] * 50, resamples=20)), {0.0})
        self.assertEqual(T.stationary_bootstrap_sharpe([0.1], resamples=5), [])

    def test_select_respects_boundaries(self):
        res = T.PortfolioResult([10, 20, 30, 40], [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0], {})
        self.assertEqual(T.select(res, None, 30)[0], [10, 20])
        self.assertEqual(T.select(res, 30, None)[0], [30, 40])


class CliTest(unittest.TestCase):
    def test_dev_stage_never_touches_holdout_and_full_stage_runs(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            for sym, seed in (("AAA", 21), ("BBB", 22)):
                with open(os.path.join(d, f"{sym}_d1.csv"), "w") as fh:
                    fh.write("time,open,high,low,close\n")
                    for t, c in make_series(700, seed=seed):
                        fh.write(f"{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%dT%H:%M:%SZ},{c},{c},{c},{c}\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(T.main(["--dir", d, "--stage", "dev"]), 0)
            out = buf.getvalue()
            self.assertIn("Holdout not touched", out)
            self.assertNotIn("HOLDOUT net", out)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(T.main(["--dir", d, "--stage", "full", "--resamples", "50"]), 0)
            self.assertIn("OVERALL:", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

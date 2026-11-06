"""Tests for the cost-probe converter (stdlib only)."""
import csv
import os
import tempfile
import unittest

import cost_model as C

FIELDS = ["run_utc", "symbol", "swap_long", "swap_short", "swap_calc_type", "pip_position", "swap_rollover_3days",
          "commission", "samples", "mid", "spread_bps_median"]


def row(symbol, run="2026-11-05T14:50:00Z", long_=-6.48, short=1.48, calc="PERCENTAGE", pip=0, mid="7587.5",
        samples=400, bps="0.53", tri="FRIDAY"):
    return dict(run_utc=run, symbol=symbol, swap_long=long_, swap_short=short, swap_calc_type=calc, pip_position=pip,
                swap_rollover_3days=tri, commission=0, samples=samples, mid=mid, spread_bps_median=bps)


class ConversionTest(unittest.TestCase):
    def test_pip_size(self):
        self.assertEqual(C.pip_size(0), 1.0)
        self.assertAlmostEqual(C.pip_size(2), 0.01)
        self.assertAlmostEqual(C.pip_size(1), 0.1)

    def test_percentage_passes_through(self):
        self.assertEqual(C.swap_annual_pct(-6.48, "PERCENTAGE", 0, 7587.5), -6.48)

    def test_pips_convert_to_annual_share_of_price(self):
        # gold: -7.51 pips x 0.1 = -0.751 USD/oz/day on 4108.715 -> -6.67 %/year
        self.assertAlmostEqual(C.swap_annual_pct(-7.51, "PIPS", 1, 4108.715), -0.751 / 4108.715 * 365 * 100, places=9)
        self.assertAlmostEqual(C.swap_annual_pct(-7.51, "PIPS", 1, 4108.715), -6.67, places=2)
        # sign is kept: a positive pips value is a credit
        self.assertGreater(C.swap_annual_pct(2.56, "PIPS", 1, 4108.715), 0)

    def test_unconvertible_cases_return_none(self):
        self.assertIsNone(C.swap_annual_pct(-1.0, "PIPS", 1, None))
        self.assertIsNone(C.swap_annual_pct(-1.0, "PIPS", 1, 0.0))
        self.assertIsNone(C.swap_annual_pct(-1.0, "POINTS", 1, 100.0))


class AggregateTest(unittest.TestCase):
    def test_spread_across_runs_typical_worst_and_one_way(self):
        rows = [row("US500", run="2026-11-05T02:00:00Z", bps="0.90"), row("US500", run="2026-11-05T08:30:00Z", bps="0.60"),
                row("US500", run="2026-11-05T14:50:00Z", bps="0.50")]
        s = C.aggregate(rows)[0]
        self.assertEqual(s["runs_with_quotes"], 3)
        self.assertAlmostEqual(s["spread_bps_typical"], 0.60)
        self.assertAlmostEqual(s["spread_bps_worst"], 0.90)
        self.assertAlmostEqual(s["one_way_bps"], 0.30)

    def test_runs_without_quotes_are_ignored_not_counted_as_zero_spread(self):
        rows = [row("XAUUSD", run="2026-11-05T02:00:00Z", samples=0, bps="", mid=""), row("XAUUSD", bps="0.49", mid="4108.7")]
        s = C.aggregate(rows)[0]
        self.assertEqual(s["runs_with_quotes"], 1)
        self.assertAlmostEqual(s["spread_bps_typical"], 0.49)
        none = C.aggregate([row("XAUUSD", samples=0, bps="", mid="")])[0]
        self.assertIsNone(none["spread_bps_typical"])
        self.assertIsNone(none["one_way_bps"])

    def test_roll_carry_flag_for_large_swaps_only(self):
        gas = row("NatGas", long_=-1.41, short=1.25, calc="PIPS", pip=2, mid="2.6885", bps="11.16")
        idx = row("US500")
        by = {s["symbol"]: s for s in C.aggregate([gas, idx])}
        self.assertTrue(by["NatGas"]["embeds_roll_carry"])
        self.assertFalse(by["US500"]["embeds_roll_carry"])
        self.assertLess(by["NatGas"]["swap_long_pct"], -100)

    def test_uses_the_latest_run_for_swap_values(self):
        rows = [row("US500", run="2026-11-05T02:00:00Z", long_=-9.0), row("US500", run="2026-11-05T14:50:00Z", long_=-6.48)]
        self.assertEqual(C.aggregate(rows)[0]["swap_long_pct"], -6.48)

    def test_load_rows_and_render_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.csv")
            with open(p, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                w.writeheader()
                w.writerow(row("US500"))
                w.writerow(row("GER40", long_=-4.94, short=-0.06, mid="26503.15", bps="0.34"))
            summary = C.aggregate(C.load_rows([p]))
        text = C.render(summary)
        self.assertEqual([s["symbol"] for s in summary], ["GER40", "US500"])  # sorted
        self.assertIn("| GER40 |", text)
        self.assertIn("-6.48%", text)
        self.assertIn("+1.48%", text)
        self.assertIn("| symbol |", text.splitlines()[0])


if __name__ == "__main__":
    unittest.main()

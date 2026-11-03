"""Offline tests for the read-only cost probe (no network, no broker)."""
import csv
import importlib
import io
import os
import types
import unittest
from unittest import mock

try:
    import ctrader_cost_probe as P
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOACommissionType, ProtoOADayOfWeek, ProtoOASwapCalculationType, ProtoOASymbol, ProtoOATradingMode)
    AVAILABLE = True
except Exception:  # twisted / ctrader-open-api not installed
    AVAILABLE = False


def reload_with(**env):
    keys = ("PETRA_COST_SYMBOLS", "PETRA_COST_SAMPLE_SECONDS", "PETRA_COST_TIMEOUT_SECONDS")
    clean = {k: v for k, v in os.environ.items() if k not in keys}
    with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
        return importlib.reload(P)


@unittest.skipUnless(AVAILABLE, "bridge dependencies (twisted, ctrader-open-api) not installed")
class CostProbeTest(unittest.TestCase):
    def tearDown(self):
        reload_with()

    def test_decode_symbol_reports_swap_and_spec_fields_with_enum_names(self):
        sym = ProtoOASymbol(symbolId=10013, digits=1, pipPosition=1, swapLong=-3.25, swapShort=1.5,
                            swapCalculationType=ProtoOASwapCalculationType.Value("PERCENTAGE"),
                            swapPeriod=1, swapTime=1260, swapRollover3Days=ProtoOADayOfWeek.Value("FRIDAY"),
                            chargeSwapAtWeekends=False, lotSize=100, minVolume=1, stepVolume=1,
                            enableShortSelling=True, commission=0,
                            commissionType=ProtoOACommissionType.Value("USD_PER_LOT"),
                            tradingMode=ProtoOATradingMode.Value("ENABLED"))
        d = P.decode_symbol(sym)
        self.assertEqual((d["swap_long"], d["swap_short"], d["swap_calc_type"]), (-3.25, 1.5, "PERCENTAGE"))
        self.assertEqual((d["swap_rollover_3days"], d["trading_mode"], d["commission_type"]),
                         ("FRIDAY", "ENABLED", "USD_PER_LOT"))
        self.assertEqual((d["digits"], d["pip_position"], d["lot_size"], d["swap_time_min"]), (1, 1, 100, 1260))
        self.assertTrue(d["short_selling"])

    def test_spot_book_handles_one_sided_updates_and_ignores_crossed_quotes(self):
        b = P.SpotBook()
        b.update(1, 500_000_000, None)       # bid 5000.00 only: no spread yet
        self.assertEqual(b.spreads, {})
        b.update(1, None, 500_040_000)       # ask 5000.40 arrives: spread 0.4
        self.assertEqual(len(b.spreads[1]), 1)
        self.assertAlmostEqual(b.spreads[1][0], 0.4, places=9)
        b.update(1, 500_100_000, None)       # bid 5001.00 above remembered ask -> crossed, ignored
        self.assertEqual(len(b.spreads[1]), 1)
        b.update(1, None, 500_160_000)       # ask 5001.60 catches up: spread 0.6
        self.assertAlmostEqual(b.spreads[1][-1], 0.6, places=9)
        b.update(2, 100_000_000, 100_010_000)
        self.assertEqual(len(b.spreads[1]), 2)  # symbols are independent
        self.assertEqual(len(b.spreads[2]), 1)

    def test_spread_stats_values_and_bps(self):
        s = P.spread_stats([0.4, 0.5, 0.6, 0.5, 0.5], 4999.75, 5000.25)
        self.assertEqual(s["samples"], 5)
        self.assertAlmostEqual(s["spread_median"], 0.5)
        self.assertAlmostEqual(s["mid"], 5000.0)
        self.assertAlmostEqual(s["spread_bps_median"], 1.0)
        self.assertLessEqual(s["spread_p10"], s["spread_median"])
        self.assertLessEqual(s["spread_median"], s["spread_p90"])
        empty = P.spread_stats([], None, None)
        self.assertEqual(empty["samples"], 0)
        self.assertIsNone(empty["spread_median"])

    def test_rows_are_merged_sorted_and_csv_roundtrips(self):
        book = P.SpotBook()
        book.update(2, 200_000_000, 200_010_000)
        resolved = {2: ("XAUUSD", "Gold"), 1: ("GER40", "German 40")}
        specs = {2: {"swap_long": -7.0, "swap_calc_type": "PIPS", "digits": 2}}
        rows = P.build_rows("2026-11-03T10:00:00Z", resolved, specs, book)
        self.assertEqual([r["symbol"] for r in rows], ["GER40", "XAUUSD"])
        self.assertEqual(rows[0]["samples"], 0)
        out = io.StringIO()
        P.write_csv(rows, out)
        parsed = list(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(list(parsed[0].keys()), P.COLUMNS)
        self.assertEqual(parsed[1]["swap_long"], "-7.000000")
        self.assertEqual(parsed[0]["spread_median"], "")  # no quotes -> blank, not zero
        self.assertEqual(parsed[1]["samples"], "1")

    def test_config_defaults_and_bounds(self):
        m = reload_with()
        self.assertIn("XAUUSD", m.SYMBOLS)
        self.assertIn("SpotBrent", m.SYMBOLS)
        self.assertEqual((m.SAMPLE_SECONDS, m.TIMEOUT_SECONDS), (90, 240))
        m = reload_with(PETRA_COST_SYMBOLS=" US500 , NatGas ", PETRA_COST_SAMPLE_SECONDS="1", PETRA_COST_TIMEOUT_SECONDS="99999")
        self.assertEqual(m.SYMBOLS, ["US500", "NatGas"])
        self.assertEqual((m.SAMPLE_SECONDS, m.TIMEOUT_SECONDS), (20, 1800))

    def test_refuses_to_run_unless_demo_dry_run_unarmed_no_submit(self):
        for bad in ({"environment": "live"}, {"dry_run": False}, {"armed": True}, {"can_submit": True}):
            stub = types.SimpleNamespace(**{"environment": "demo", "dry_run": True, "armed": False, "can_submit": False, **bad})
            with mock.patch.object(P, "load_ctrader_settings", return_value=stub), \
                    mock.patch.object(P, "Client", side_effect=AssertionError("must not connect")):
                self.assertEqual(P.main(), 2, bad)

    def test_source_imports_no_order_messages(self):
        src = open(P.__file__).read()
        for forbidden in ("ProtoOANewOrderReq", "ProtoOAClosePositionReq", "ProtoOAAmendOrderReq",
                          "ProtoOAAmendPositionSLTPReq", "ProtoOACancelOrderReq"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()

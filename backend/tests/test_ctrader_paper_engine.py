"""Offline tests for Petra's cTrader paper engine (open / hold / stop-loss / take-profit / close).

No network and no broker: the autonomous analysis is replaced with scripted data and the
ledger/journal are written to a temporary directory.
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import ctrader_paper_engine as E

# 2026-11-02 00:07:30Z -> inside the 00:05 M5 bar
OPEN_TS = int(datetime(2026, 11, 2, 0, 7, 30, tzinfo=timezone.utc).timestamp())
ENTRY_BAR = (OPEN_TS // 300) * 300
BAR1 = ENTRY_BAR + 300
BAR2 = ENTRY_BAR + 600


def candle(t, o, h, l, c=None):
    return {"time": t, "open": o, "high": h, "low": l, "close": c if c is not None else o}


def analysis(price, decision="NO_TRADE", final=None, actionable=False, conf=0.8, floor=0.6,
             sl=None, tp=None, candles=None):
    final = final or decision
    sell = decision == "SELL"
    return {
        "status": "ok",
        "final_decision": final,
        "shadow_actionable": actionable,
        "broker_symbol": "US500",
        "market_price": price,
        "candles": candles or [],
        "analysis": {
            "price": price,
            "decision": decision,
            "confidence": conf,
            "confidence_floor": floor,
            "suggested_stop_loss": sl if sl is not None else (price + 10 if sell else price - 10),
            "suggested_take_profit": tp if tp is not None else (price - 20 if sell else price + 20),
            "reason": "test",
        },
    }


class PaperEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(E, "STATE_FILE", base / "state.json"),
            mock.patch.object(E, "JOURNAL_FILE", base / "journal.jsonl"),
            mock.patch.object(E, "STOP_SLIPPAGE", 0.0),
            mock.patch.object(E, "_now", lambda: datetime.fromtimestamp(OPEN_TS, timezone.utc)),
        ]
        for p in self.patches:
            p.start()
        self.base = base

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def cycle(self, data):
        with mock.patch.object(E, "_run_analysis", return_value=data):
            return E.run_cycle()

    def open_position(self, side="BUY", price=7000.0):
        r = self.cycle(analysis(price, side, actionable=True))
        self.assertEqual(r["last_action"], f"OPEN_{side}")
        return r

    # ---- opening -------------------------------------------------------------------
    def test_open_buy_sets_stop_target_and_logs_event(self):
        r = self.open_position("BUY")
        pos = r["position"]
        self.assertEqual((pos["entry_price"], pos["stop_loss"], pos["take_profit"]), (7000.0, 6990.0, 7020.0))
        self.assertEqual([e["event"] for e in r["events"]], ["OPEN"])
        self.assertEqual(r["events"][0]["stop_loss"], 6990.0)
        self.assertLessEqual(pos["notional_usd"], E.MAX_POSITION_NOTIONAL)

    def test_no_open_when_not_actionable_or_no_trade(self):
        self.assertIsNone(self.cycle(analysis(7000, "BUY", actionable=False))["position"])
        self.assertIsNone(self.cycle(analysis(7000, "NO_TRADE", final="NO_TRADE", actionable=True))["position"])

    def test_invalid_stop_distance_rejected(self):
        r = self.cycle(analysis(7000, "BUY", actionable=True, sl=7000.0))
        self.assertIsNone(r["position"])
        self.assertEqual(r["last_action"], "NO_ACTION")

    def test_only_one_position_at_a_time(self):
        self.open_position("BUY")
        r = self.cycle(analysis(7003, "BUY", actionable=True))
        self.assertEqual(r["last_action"], "HOLD")
        self.assertEqual(r["position"]["entry_price"], 7000.0)
        self.assertEqual(len([e for e in r["events"] if e["event"] == "OPEN"]), 0)

    def test_hold_inside_range(self):
        self.open_position("BUY")
        r = self.cycle(analysis(6995))
        self.assertEqual(r["last_action"], "HOLD")
        self.assertLess(r["unrealized_pnl"], 0)
        self.assertEqual(r["realized_pnl"], 0.0)

    # ---- stop loss -----------------------------------------------------------------
    def test_buy_stop_loss_by_cycle_price_without_candles(self):
        self.open_position("BUY")
        r = self.cycle(analysis(6989))
        self.assertEqual((r["last_action"], r["last_exit_reason"]), ("CLOSE", "STOP_LOSS"))
        self.assertIsNone(r["position"])
        self.assertLess(r["realized_pnl"], 0)
        self.assertEqual(r["events"][0]["exit_price"], 6989)  # no bars: cycle price (conservative)

    def test_sell_stop_loss_by_cycle_price(self):
        self.open_position("SELL")
        r = self.cycle(analysis(7010.5))
        self.assertEqual(r["last_exit_reason"], "STOP_LOSS")
        self.assertLess(r["realized_pnl"], 0)

    def test_buy_intra_cycle_wick_through_stop_is_caught_and_filled_at_stop(self):
        self.open_position("BUY")
        # price recovered by cycle time, but the bar wicked through the stop
        r = self.cycle(analysis(7002, candles=[candle(BAR1, 7001, 7004, 6985, 7002)]))
        self.assertEqual(r["last_exit_reason"], "STOP_LOSS")
        ev = r["events"][0]
        self.assertEqual(ev["exit_price"], 6990.0)
        self.assertEqual(ev["trigger_bar_time"], BAR1)
        expected = round((6990.0 - 7000.0) * ev["quantity"], 6)
        self.assertEqual(ev["pnl"], expected)
        self.assertEqual(r["realized_pnl"], expected)

    def test_sell_intra_cycle_wick_through_stop(self):
        self.open_position("SELL")
        r = self.cycle(analysis(6998, candles=[candle(BAR1, 6999, 7015, 6997, 6998)]))
        self.assertEqual(r["events"][0]["exit_price"], 7010.0)
        self.assertEqual(r["last_exit_reason"], "STOP_LOSS")

    def test_gap_through_stop_fills_at_bar_open_not_stop(self):
        self.open_position("BUY")
        r = self.cycle(analysis(6951, candles=[candle(BAR1, 6950, 6955, 6945, 6951)]))
        self.assertEqual(r["events"][0]["exit_price"], 6950)
        self.assertLess(r["events"][0]["pnl"], (6990.0 - 7000.0) * r["events"][0]["quantity"])

    def test_stop_slippage_applied_against_position(self):
        self.open_position("BUY")
        with mock.patch.object(E, "STOP_SLIPPAGE", 0.5):
            r = self.cycle(analysis(7002, candles=[candle(BAR1, 7001, 7004, 6985)]))
        self.assertEqual(r["events"][0]["exit_price"], 6989.5)
        r2 = None
        Path(E.STATE_FILE).unlink()
        self.open_position("SELL")
        with mock.patch.object(E, "STOP_SLIPPAGE", 0.5):
            r2 = self.cycle(analysis(6998, candles=[candle(BAR1, 6999, 7015, 6997)]))
        self.assertEqual(r2["events"][0]["exit_price"], 7010.5)

    def test_entry_bar_wick_is_ignored_next_bar_is_checked(self):
        self.open_position("BUY")
        entry_bar_wick = candle(ENTRY_BAR, 7000, 7001, 6900, 7000)  # includes pre-entry prices
        r = self.cycle(analysis(7001, candles=[entry_bar_wick]))
        self.assertEqual(r["last_action"], "HOLD")
        r = self.cycle(analysis(7001, candles=[entry_bar_wick, candle(BAR1, 7001, 7003, 6989)]))
        self.assertEqual(r["last_exit_reason"], "STOP_LOSS")

    def test_stop_and_target_same_bar_assumes_stop_first(self):
        self.open_position("BUY")
        r = self.cycle(analysis(7005, candles=[candle(BAR1, 7001, 7025, 6985)]))
        self.assertEqual(r["last_exit_reason"], "STOP_LOSS")

    def test_earliest_triggering_bar_wins(self):
        self.open_position("BUY")
        bars = [candle(BAR1, 7001, 7022, 6995), candle(BAR2, 7010, 7012, 6980)]
        r = self.cycle(analysis(7000, candles=bars))
        self.assertEqual(r["last_exit_reason"], "TAKE_PROFIT")
        self.assertEqual(r["events"][0]["trigger_bar_time"], BAR1)

    # ---- take profit ---------------------------------------------------------------
    def test_buy_take_profit_by_wick_fills_at_target(self):
        self.open_position("BUY")
        r = self.cycle(analysis(7010, candles=[candle(BAR1, 7005, 7025, 7003, 7010)]))
        self.assertEqual((r["last_exit_reason"], r["events"][0]["exit_price"]), ("TAKE_PROFIT", 7020.0))
        self.assertGreater(r["realized_pnl"], 0)
        self.assertEqual(r["wins"], 1)

    def test_sell_take_profit_by_cycle_price(self):
        self.open_position("SELL")
        r = self.cycle(analysis(6979))
        self.assertEqual(r["last_exit_reason"], "TAKE_PROFIT")
        self.assertGreater(r["realized_pnl"], 0)

    # ---- close on opposite signal / flip --------------------------------------------
    def test_weak_opposite_signal_ignored_strong_closes(self):
        self.open_position("BUY")
        self.assertEqual(self.cycle(analysis(7002, "SELL", conf=0.3))["last_action"], "HOLD")
        r = self.cycle(analysis(7001, "SELL", conf=0.9))
        self.assertEqual((r["last_action"], r["last_exit_reason"]), ("CLOSE", "OPPOSITE_SIGNAL"))
        self.assertIsNone(r["position"])

    def test_flip_closes_then_opens_other_side_in_one_cycle(self):
        self.open_position("BUY")
        r = self.cycle(analysis(7001, "SELL", actionable=True, conf=0.9))
        self.assertEqual(r["last_action"], "OPEN_SELL")
        self.assertEqual([e["event"] for e in r["events"]], ["CLOSE", "OPEN"])
        self.assertEqual(r["position"]["side"], "SELL")

    def test_stop_then_reentry_same_cycle(self):
        self.open_position("BUY")
        r = self.cycle(analysis(6985, "BUY", actionable=True, candles=[candle(BAR1, 6995, 6996, 6984, 6985)]))
        self.assertEqual([e["event"] for e in r["events"]], ["CLOSE", "OPEN"])
        self.assertEqual(r["events"][0]["exit_reason"], "STOP_LOSS")

    # ---- accounting / persistence / safety -----------------------------------------
    def test_accounting_reconciles_over_a_sequence(self):
        self.open_position("BUY")
        seq = [analysis(7004), analysis(7002, candles=[candle(BAR1, 7001, 7003, 6985)]),
               analysis(7000, "SELL", actionable=True),
               analysis(6990, candles=[candle(BAR2, 6995, 6996, 6988)])]
        for data in seq:
            r = self.cycle(data)
            self.assertAlmostEqual(r["realized_pnl"], r["balance"] - E.STARTING_BALANCE, places=5)
            self.assertAlmostEqual(r["equity"], r["balance"] + r["unrealized_pnl"], places=5)
        self.assertAlmostEqual(r["realized_pnl"], sum(t["pnl"] for t in r["trade_history"]), places=5)

    def test_trade_history_and_journal_record_full_detail(self):
        self.open_position("BUY")
        r = self.cycle(analysis(7002, candles=[candle(BAR1, 7001, 7004, 6985)]))
        t = r["trade_history"][0]
        for key in ("side", "entry_price", "exit_price", "stop_loss", "take_profit", "pnl", "exit_reason"):
            self.assertIn(key, t)
        lines = [json.loads(x) for x in E.JOURNAL_FILE.read_text().splitlines()]
        self.assertEqual([x["event"] for x in lines], ["OPEN", "CLOSE"])
        self.assertEqual(lines[1]["exit_reason"], "STOP_LOSS")

    def test_state_survives_reload_and_save_is_atomic(self):
        self.open_position("BUY")
        self.assertFalse(E.STATE_FILE.with_name(E.STATE_FILE.name + ".tmp").exists())
        r = self.cycle(analysis(7005))  # new "process": state reloaded from disk
        self.assertEqual(r["position"]["entry_price"], 7000.0)

    def test_persistence_flag(self):
        self.assertFalse(E._state_is_persistent(Path("/tmp/x/state.json")))
        self.assertTrue(E._state_is_persistent(Path("/var/data/state.json")))

    def test_never_places_broker_orders(self):
        r = self.open_position("BUY")
        self.assertFalse(r["orders_enabled"])
        self.assertFalse(r["broker_execution"])
        self.assertFalse(r["paper_profile"]["live_execution"])
        source = Path(E.__file__).read_text()
        for forbidden in ("ctrader_open_api", "ProtoOANewOrderReq", "ProtoOAClosePositionReq"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

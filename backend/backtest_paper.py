"""Offline backtest for Petra's paper strategy: cooldown and flip-handling variants.

Replays historical M5 bars through the *real* signal code (`ctrader_autonomous_bridge._analyze`)
and the *real* paper engine (`ctrader_paper_engine.run_cycle`, including its candle-based
stop/target replay), once per bar. It never touches the network or a broker.

Variants are applied by the harness around the engine, so production behaviour is unchanged:
  cooldown_bars      no new entry for N bars after ANY close
  flip_cooldown_bars no new entry for N bars after an OPPOSITE_SIGNAL close (close, don't reverse)
  min_hold_bars      ignore opposite signals until the position is N bars old (stop/target stay live)
  flip_conf_extra    an opposite signal must beat confidence_floor + extra to close

Assumptions (see README of the report): cycles run at bar close on completed bars; the broker
account is flat (the live bridge only marks a signal actionable when it is); fixed per-round-trip
spread cost in index points.

Usage:
  python backtest_paper.py bars.csv [--train-frac 0.6] [--spread 0.5]
CSV columns: time (epoch s/ms or ISO), open, high, low, close.
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

BAR_SECONDS = 300
WARMUP = 72  # matches the bridge's default PETRA_CTRADER_BAR_COUNT
UI_CANDLES = 60  # candles handed to the engine for stop/target replay


@dataclass(frozen=True)
class Variant:
    name: str = "baseline"
    cooldown_bars: int = 0
    flip_cooldown_bars: int = 0
    min_hold_bars: int = 0
    flip_conf_extra: float = 0.0


@dataclass
class Trade:
    side: str
    entry: float
    exit: float
    exit_reason: str
    bars_held: int
    qty: float
    pnl_usd: float
    points: float
    opened_ts: int = 0
    closed_ts: int = 0


def _modules():
    import ctrader_autonomous_bridge as bridge
    import ctrader_paper_engine as engine
    return bridge, engine


def load_bars(path: str) -> list[dict[str, float]]:
    def parse_time(raw: str) -> int:
        raw = raw.strip()
        try:
            v = float(raw)
            return int(v / 1000) if v > 1e11 else int(v)
        except ValueError:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())

    rows: dict[int, dict[str, float]] = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        tkey = next((k for k in ("time", "timestamp", "datetime", "date") if k in reader.fieldnames), None)
        if not tkey or not {"open", "high", "low", "close"} <= set(reader.fieldnames):
            raise SystemExit("CSV needs columns: time, open, high, low, close")
        for r in reader:
            t = parse_time(r[tkey])
            rows[t] = {"time": t, **{k: float(r[k]) for k in ("open", "high", "low", "close")}}
    bars = [rows[t] for t in sorted(rows)]
    for b in bars:
        b["ts_minutes"] = b["time"] // 60
        b["volume"] = 0.0
    return bars


def synthetic_bars(n: int, seed: int = 7, start_price: float = 6800.0) -> list[dict[str, float]]:
    """Regime-switching random walk. For testing the harness mechanics ONLY."""
    rng = random.Random(seed)
    t0 = int(datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc).timestamp())
    price, drift, left = start_price, 0.0, 0
    out = []
    for i in range(n):
        if left <= 0:
            drift = rng.choice([-0.35, 0.0, 0.0, 0.35])
            left = rng.randint(30, 200)
        left -= 1
        o = price
        pts = [o]
        for _ in range(4):
            pts.append(pts[-1] + drift + rng.gauss(0, 1.6))
        c = pts[-1]
        h = max(pts) + abs(rng.gauss(0, 0.5))
        l = min(pts) - abs(rng.gauss(0, 0.5))
        t = t0 + i * BAR_SECONDS
        out.append({"time": t, "ts_minutes": t // 60, "open": o, "high": h, "low": l, "close": c, "volume": 0.0})
        price = c
    return out


def run_variant(bars: list[dict[str, float]], variant: Variant, *, spread_pts: float = 0.0) -> list[Trade]:
    bridge, E = _modules()
    trades: list[Trade] = []
    cd_any = cd_flip = 0
    open_age = 0
    clock = {"t": 0}
    tmp = tempfile.TemporaryDirectory()
    base = Path(tmp.name)
    patches = [
        mock.patch.object(E, "STATE_FILE", base / "state.json"),
        mock.patch.object(E, "JOURNAL_FILE", base / "journal.jsonl"),
        mock.patch.object(E, "STOP_SLIPPAGE", 0.0),
        mock.patch.object(E, "_now", lambda: datetime.fromtimestamp(clock["t"], timezone.utc)),
    ]
    for p in patches:
        p.start()
    try:
        for i in range(WARMUP, len(bars)):
            window = bars[i - WARMUP + 1: i + 1]
            bar = bars[i]
            clock["t"] = bar["time"] + BAR_SECONDS - 1  # cycle at bar close, still inside bar i so bar i+1 is replayed
            a = bridge._analyze(window)
            decision = a["decision"]
            conf = float(a["confidence"])
            floor = float(a["confidence_floor"])
            state = E._load_state()
            pos = state.get("position")
            candles = [{k: b[k] for k in ("time", "open", "high", "low", "close")} for b in bars[max(0, i - UI_CANDLES + 1): i + 1]]
            actionable = decision in {"BUY", "SELL"}

            will_close_by_level = False
            will_flip_close = False
            eff_floor = floor + variant.flip_conf_extra
            if pos:
                opposite = (pos["side"] == "BUY" and decision == "SELL") or (pos["side"] == "SELL" and decision == "BUY")
                if E._find_protective_exit(pos, candles):
                    will_close_by_level = True
                elif opposite:
                    if open_age < variant.min_hold_bars:
                        decision, conf = "NO_TRADE", 0.0  # signal ignored during min-hold
                        actionable = False
                    elif conf >= eff_floor:
                        will_flip_close = True
            if will_close_by_level and variant.cooldown_bars > 0:
                actionable = False
            if will_flip_close and (variant.flip_cooldown_bars > 0 or variant.cooldown_bars > 0):
                actionable = False
            if cd_any > 0 or cd_flip > 0:
                actionable = False

            payload = {
                "status": "ok",
                "final_decision": decision if actionable else "NO_TRADE",
                "shadow_actionable": actionable,
                "broker_symbol": "US500",
                "market_price": a["price"],
                "candles": candles,
                "analysis": {**a, "decision": decision, "confidence": conf, "confidence_floor": eff_floor},
            }
            with mock.patch.object(E, "_run_analysis", return_value=payload):
                result = E.run_cycle()

            closed_now = False
            for ev in result["events"]:
                if ev["event"] == "CLOSE":
                    closed_now = True
                    pts = (ev["exit_price"] - ev["entry_price"]) * (1 if ev["side"] == "BUY" else -1)
                    opened = E._iso_to_epoch_seconds(ev["opened_at"]) or clock["t"]
                    trades.append(Trade(ev["side"], ev["entry_price"], ev["exit_price"], ev["exit_reason"],
                                        max(0, (clock["t"] - opened) // BAR_SECONDS), ev["quantity"],
                                        ev["pnl"] - spread_pts * ev["quantity"], pts - spread_pts,
                                        opened, clock["t"]))
                    if variant.cooldown_bars:
                        cd_any = variant.cooldown_bars
                    if ev["exit_reason"] == "OPPOSITE_SIGNAL" and variant.flip_cooldown_bars:
                        cd_flip = variant.flip_cooldown_bars
            opened_now = any(ev["event"] == "OPEN" for ev in result["events"])
            if opened_now:
                open_age = 0
            elif result["position"]:
                open_age += 1
            if not closed_now:
                cd_any = max(0, cd_any - 1)
                cd_flip = max(0, cd_flip - 1)
    finally:
        for p in patches:
            p.stop()
        tmp.cleanup()
    return trades


def metrics(trades: list[Trade], n_bars: int, seed: int = 11) -> dict[str, Any]:
    n = len(trades)
    pts = [t.points for t in trades]
    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    equity, peak, mdd = 0.0, 0.0, 0.0
    for p in pts:
        equity += p
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    ci = (None, None)
    if n >= 10:
        rng = random.Random(seed)
        means = sorted(statistics.fmean(rng.choices(pts, k=n)) for _ in range(1000))
        ci = (means[25], means[974])
    days = max(1e-9, (n_bars - WARMUP) * BAR_SECONDS / 86400.0)
    return {
        "trades": n,
        "win_rate": (len(wins) / n) if n else 0.0,
        "avg_win": statistics.fmean(wins) if wins else 0.0,
        "avg_loss": statistics.fmean(losses) if losses else 0.0,
        "expectancy_pts": statistics.fmean(pts) if n else 0.0,
        "ci95_lo": ci[0], "ci95_hi": ci[1],
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win else 0.0,
        "total_pts": sum(pts),
        "max_dd_pts": mdd,
        "trades_per_day": n / days,
        "flips": sum(1 for t in trades if t.exit_reason == "OPPOSITE_SIGNAL"),
        "stops": sum(1 for t in trades if t.exit_reason == "STOP_LOSS"),
        "targets": sum(1 for t in trades if t.exit_reason == "TAKE_PROFIT"),
    }


DEFAULT_GRID = [
    Variant("baseline"),
    Variant("cooldown 3", cooldown_bars=3),
    Variant("cooldown 6", cooldown_bars=6),
    Variant("cooldown 12", cooldown_bars=12),
    Variant("no-reverse 6", flip_cooldown_bars=6),
    Variant("no-reverse 12", flip_cooldown_bars=12),
    Variant("min-hold 3", min_hold_bars=3),
    Variant("min-hold 6", min_hold_bars=6),
    Variant("flip conf +0.07", flip_conf_extra=0.07),
    Variant("min-hold 6 + no-reverse 6", min_hold_bars=6, flip_cooldown_bars=6),
    Variant("min-hold 6 + cooldown 6 + conf +0.07", min_hold_bars=6, cooldown_bars=6, flip_conf_extra=0.07),
]


def _row(name: str, m: dict[str, Any]) -> str:
    ci = "n/a" if m["ci95_lo"] is None else f"[{m['ci95_lo']:+.2f},{m['ci95_hi']:+.2f}]"
    return (f"{name:38s} {m['trades']:5d} {m['win_rate']*100:5.1f}% {m['avg_win']:+7.2f} {m['avg_loss']:+7.2f} "
            f"{m['expectancy_pts']:+7.3f} {ci:>16s} {m['profit_factor']:5.2f} {m['total_pts']:+9.1f} "
            f"{m['max_dd_pts']:7.1f} {m['trades_per_day']:5.1f} {m['flips']:4d}/{m['stops']:4d}/{m['targets']:4d}")


HEADER = (f"{'variant':38s} {'n':>5s} {'win%':>6s} {'avgWin':>7s} {'avgLoss':>7s} {'E[pts]':>7s} "
          f"{'95% CI (E)':>16s} {'PF':>5s} {'total':>9s} {'maxDD':>7s} {'/day':>5s} flip/stop/tgt")


def run_grid(bars, grid, spread_pts, train_frac):
    cut = int(len(bars) * train_frac)
    segments = {"TRAIN": bars[:cut], "TEST": bars[cut - WARMUP:]}  # test segment re-warms its indicators
    out = {}
    for seg, seg_bars in segments.items():
        print(f"\n== {seg}: {len(seg_bars)} bars, spread {spread_pts} pt/round-trip ==\n{HEADER}")
        for v in grid:
            m = metrics(run_variant(seg_bars, v, spread_pts=spread_pts), len(seg_bars))
            out[(seg, v.name)] = m
            print(_row(v.name, m))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--train-frac", type=float, default=0.6)
    ap.add_argument("--spread", type=float, default=0.5, help="index points per round trip")
    args = ap.parse_args(argv)
    bars = load_bars(args.csv)
    if len(bars) < 2000:
        print(f"WARNING: only {len(bars)} bars; results will be inconclusive", file=sys.stderr)
    run_grid(bars, DEFAULT_GRID, args.spread, args.train_frac)
    print("\nRead TEST rows only for any decision; a variant is credible only if its CI95 excludes 0 "
          "and it beats baseline on TEST, not just TRAIN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

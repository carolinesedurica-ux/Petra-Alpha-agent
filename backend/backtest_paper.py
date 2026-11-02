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
from zoneinfo import ZoneInfo

BAR_SECONDS = 300
ET = ZoneInfo("America/New_York")
MIN_STOP_FRAC = 0.0008  # same floor the production strategy uses
WARMUP = 72  # matches the bridge's default PETRA_CTRADER_BAR_COUNT
UI_CANDLES = 60  # candles handed to the engine for stop/target replay


@dataclass(frozen=True)
class Variant:
    name: str = "baseline"
    cooldown_bars: int = 0
    flip_cooldown_bars: int = 0
    min_hold_bars: int = 0
    flip_conf_extra: float = 0.0
    # --- signal-level experiments (applied to the analysis before the engine sees it)
    min_trend_strength: float = 0.0   # require trend_strength >= this
    htf_ema_period: int = 0           # require price on the same side of this long EMA (0 = off)
    hours_utc: tuple[int, int] | None = None  # only enter when bar-close hour in [start, end)
    min_atr: float = 0.0              # skip quiet markets (ATR14 in index points)
    stop_mult: float = 1.0            # scale the strategy's stop distance
    rr: float | None = None           # target = rr * stop distance (None keeps the strategy's 1.6)
    # --- alternative signal families
    signal: str = "strategy"          # "strategy" | "donchian" | "orb"
    n_lookback: int = 0               # donchian lookback in bars
    et_hours_only: bool = False       # donchian: only US cash hours (09:30-16:00 ET)
    or_minutes: int = 0               # orb: opening-range length in minutes
    orb_rr: float = 1.6               # orb: target multiple of the range-based stop
    orb_cutoff_et: int = 11 * 60 + 30  # orb: no entries after this ET minute-of-day
    flat_et: int = 15 * 60 + 55       # orb: forced flat at/after this ET minute-of-day


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
    risk_pts: float = 0.0


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


def et_parts(ts: int) -> tuple[Any, int]:
    """(ET calendar date, ET minute-of-day) for a UTC epoch second; DST-aware."""
    dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(ET)
    return dt.date(), dt.hour * 60 + dt.minute


def _signal_dict(decision: str, price: float, stop: float | None, target: float | None, reason: str) -> dict[str, Any]:
    return {"decision": decision, "confidence": 0.9 if decision != "NO_TRADE" else 0.0,
            "confidence_floor": 0.68, "price": round(price, 5), "suggested_stop_loss": stop,
            "suggested_take_profit": target, "reason": reason}


def _atr14(bars: list[dict[str, float]]) -> float:
    trs = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
           for p, b in zip(bars, bars[1:])]
    sample = trs[-14:]
    return sum(sample) / len(sample) if sample else 0.0


def donchian_signal(bars: list[dict[str, float]], i: int, n: int, *, et_hours_only: bool = False,
                    bar_close_ts: int | None = None) -> dict[str, Any]:
    """Close beyond the previous n bars' extreme. Stop/target use the production strategy's geometry."""
    last = bars[i]["close"]
    if i < n + 14:
        return _signal_dict("NO_TRADE", last, None, None, "warming up")
    if et_hours_only and bar_close_ts is not None:
        _, mod = et_parts(bar_close_ts)
        if not (9 * 60 + 30 <= mod < 16 * 60):
            return _signal_dict("NO_TRADE", last, None, None, "outside US cash hours")
    prior = bars[i - n: i]
    hi, lo = max(b["high"] for b in prior), min(b["low"] for b in prior)
    if last > hi:
        decision = "BUY"
    elif last < lo:
        decision = "SELL"
    else:
        return _signal_dict("NO_TRADE", last, None, None, "inside range")
    atr = _atr14(bars[max(0, i - 40): i + 1])
    stop_dist = max(atr * 1.25, last * MIN_STOP_FRAC)
    sign = 1 if decision == "BUY" else -1
    return _signal_dict(decision, last, round(last - sign * stop_dist, 5),
                        round(last + sign * stop_dist * 1.6, 5), f"{n}-bar breakout")


def orb_signal(bars: list[dict[str, float]], i: int, or_minutes: int, rr: float, *,
               cutoff_et: int = 11 * 60 + 30, traded_day: Any = None) -> dict[str, Any]:
    """Opening-range breakout. bars[i] is the bar whose close triggers the cycle."""
    last = bars[i]["close"]
    day, close_mod = et_parts(bars[i]["time"] + BAR_SECONDS)
    open_mod = 9 * 60 + 30
    range_end = open_mod + or_minutes
    no = lambda why: _signal_dict("NO_TRADE", last, None, None, why)  # noqa: E731
    if traded_day == day:
        return no("already traded today")
    if close_mod <= range_end:
        return no("opening range still forming")  # the bar that completes the range cannot also break it
    if close_mod > cutoff_et:
        return no("past entry cutoff")
    # opening-range bars: ET date == day and bar open in [09:30, 09:30 + or_minutes)
    rng = []
    for j in range(i, -1, -1):
        d, m = et_parts(bars[j]["time"])
        if d != day:
            break
        if open_mod <= m < range_end:
            rng.append(bars[j])
        elif m < open_mod:
            break
    expected = or_minutes // (BAR_SECONDS // 60)
    if len(rng) < expected:
        return no("incomplete opening range")
    hi, lo = max(b["high"] for b in rng), min(b["low"] for b in rng)
    if last > hi:
        decision, stop_base = "BUY", lo
    elif last < lo:
        decision, stop_base = "SELL", hi
    else:
        return no("inside opening range")
    stop_dist = max(abs(last - stop_base), last * MIN_STOP_FRAC)
    sign = 1 if decision == "BUY" else -1
    return _signal_dict(decision, last, round(last - sign * stop_dist, 5),
                        round(last + sign * stop_dist * rr, 5), f"{or_minutes}m opening-range breakout")


def apply_signal_filters(a: dict[str, Any], variant: Variant, *, closes_long: list[float] | None,
                         bar_close_ts: int) -> dict[str, Any]:
    """Return a copy of the analysis with the variant's signal filters / stop geometry applied."""
    out = dict(a)
    decision = out["decision"]
    if decision not in {"BUY", "SELL"}:
        return out

    def block() -> dict[str, Any]:
        out.update(decision="NO_TRADE", confidence=0.0, suggested_stop_loss=None, suggested_take_profit=None)
        return out

    if variant.min_trend_strength and float(out.get("trend_strength", 0.0)) < variant.min_trend_strength:
        return block()
    if variant.min_atr and float(out.get("atr_14", 0.0)) < variant.min_atr:
        return block()
    if variant.hours_utc is not None:
        hour = datetime.fromtimestamp(bar_close_ts, timezone.utc).hour
        lo, hi = variant.hours_utc
        if not (lo <= hour < hi):
            return block()
    if variant.htf_ema_period:
        if closes_long is None or len(closes_long) < variant.htf_ema_period:
            return block()
        alpha = 2.0 / (variant.htf_ema_period + 1.0)
        ema = closes_long[0]
        for c in closes_long[1:]:
            ema = alpha * c + (1.0 - alpha) * ema
        last = closes_long[-1]
        if (decision == "BUY" and last <= ema) or (decision == "SELL" and last >= ema):
            return block()
    if variant.stop_mult != 1.0 or variant.rr is not None:
        price = float(out["price"])
        stop_dist = abs(price - float(out["suggested_stop_loss"])) * variant.stop_mult
        target_dist = stop_dist * (variant.rr if variant.rr is not None else 1.6)
        sign = 1 if decision == "BUY" else -1
        out["suggested_stop_loss"] = round(price - sign * stop_dist, 5)
        out["suggested_take_profit"] = round(price + sign * target_dist, 5)
    return out


def run_variant(bars: list[dict[str, float]], variant: Variant, *, spread_pts: float = 0.0) -> list[Trade]:
    bridge, E = _modules()
    trades: list[Trade] = []
    cd_any = cd_flip = 0
    open_age = 0
    clock = {"t": 0}
    traded_day = None

    def record(ev: dict[str, Any]) -> None:
        pts = (ev["exit_price"] - ev["entry_price"]) * (1 if ev["side"] == "BUY" else -1)
        opened = E._iso_to_epoch_seconds(ev["opened_at"]) or clock["t"]
        trades.append(Trade(ev["side"], ev["entry_price"], ev["exit_price"], ev["exit_reason"],
                            max(0, (clock["t"] - opened) // BAR_SECONDS), ev["quantity"],
                            ev["pnl"] - spread_pts * ev["quantity"], pts - spread_pts,
                            opened, clock["t"], abs(ev["entry_price"] - float(ev["stop_loss"]))))

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
            bar_close_ts = bar["time"] + BAR_SECONDS
            if variant.signal == "orb":
                st = E._load_state()
                held = st.get("position")
                if held:
                    held_day, _ = et_parts(E._iso_to_epoch_seconds(held["opened_at"]) or bar_close_ts)
                    day_now, mod_now = et_parts(bar_close_ts)
                    flat_price = None
                    if day_now != held_day:
                        flat_price = bars[i - 1]["close"]  # next day started first: last known price
                    elif mod_now >= variant.flat_et:
                        flat_price = bar["close"]
                    if flat_price is not None:
                        forced: list[dict[str, Any]] = []
                        E._close_position(st, flat_price, "forced flat (end of session)", "TIME_EXIT", forced)
                        E._save_state(st)
                        for ev in forced:
                            record(ev)
            if variant.signal == "donchian":
                a = donchian_signal(bars, i, variant.n_lookback, et_hours_only=variant.et_hours_only,
                                    bar_close_ts=bar_close_ts)
            elif variant.signal == "orb":
                a = orb_signal(bars, i, variant.or_minutes, variant.orb_rr,
                               cutoff_et=variant.orb_cutoff_et, traded_day=traded_day)
            else:
                a = bridge._analyze(window)
            longn = variant.htf_ema_period
            a = apply_signal_filters(
                a, variant,
                closes_long=[b["close"] for b in bars[i - longn + 1: i + 1]] if longn and i + 1 >= longn else None,
                bar_close_ts=bar["time"] + BAR_SECONDS)
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
                    record(ev)
                    if variant.cooldown_bars:
                        cd_any = variant.cooldown_bars
                    if ev["exit_reason"] == "OPPOSITE_SIGNAL" and variant.flip_cooldown_bars:
                        cd_flip = variant.flip_cooldown_bars
            opened_now = any(ev["event"] == "OPEN" for ev in result["events"])
            if opened_now and variant.signal == "orb":
                traded_day = et_parts(bar_close_ts)[0]
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
    rs = [t.points / t.risk_pts for t in trades if t.risk_pts > 0]
    return {
        "expectancy_r": statistics.fmean(rs) if rs else 0.0,
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
        "times": sum(1 for t in trades if t.exit_reason == "TIME_EXIT"),
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


SIGNAL_GRID = [
    Variant("baseline"),
    Variant("trend strength >= 0.4", min_trend_strength=0.4),
    Variant("trend strength >= 0.7", min_trend_strength=0.7),
    Variant("HTF EMA200 agrees", htf_ema_period=200),
    Variant("US hours 13-20 UTC", hours_utc=(13, 20)),
    Variant("EU+US hours 07-20 UTC", hours_utc=(7, 20)),
    Variant("ATR >= 3.0", min_atr=3.0),
    Variant("ATR >= 4.5", min_atr=4.5),
    Variant("RR 1.0", rr=1.0),
    Variant("RR 2.5", rr=2.5),
    Variant("stop x1.5 (RR 1.6)", stop_mult=1.5),
    Variant("stop x2.0 (RR 1.6)", stop_mult=2.0),
    Variant("stop x1.0, RR 3.0", rr=3.0),
]


BREAKOUT_GRID = [
    Variant("baseline"),
    Variant("donchian 12", signal="donchian", n_lookback=12),
    Variant("donchian 24", signal="donchian", n_lookback=24),
    Variant("donchian 48", signal="donchian", n_lookback=48),
    Variant("donchian 96", signal="donchian", n_lookback=96),
    Variant("donchian 48, US cash hours", signal="donchian", n_lookback=48, et_hours_only=True),
    Variant("ORB 15m rr1.0", signal="orb", or_minutes=15, orb_rr=1.0),
    Variant("ORB 15m rr1.6", signal="orb", or_minutes=15, orb_rr=1.6),
    Variant("ORB 30m rr1.0", signal="orb", or_minutes=30, orb_rr=1.0),
    Variant("ORB 30m rr1.6", signal="orb", or_minutes=30, orb_rr=1.6),
    Variant("ORB 60m rr1.0", signal="orb", or_minutes=60, orb_rr=1.0),
    Variant("ORB 60m rr1.6", signal="orb", or_minutes=60, orb_rr=1.6),
]


def _row(name: str, m: dict[str, Any]) -> str:
    ci = "n/a" if m["ci95_lo"] is None else f"[{m['ci95_lo']:+.2f},{m['ci95_hi']:+.2f}]"
    return (f"{name:38s} {m['trades']:5d} {m['win_rate']*100:5.1f}% {m['avg_win']:+7.2f} {m['avg_loss']:+7.2f} "
            f"{m['expectancy_pts']:+7.3f} {m['expectancy_r']:+6.3f} {ci:>16s} {m['profit_factor']:5.2f} {m['total_pts']:+9.1f} "
            f"{m['max_dd_pts']:7.1f} {m['trades_per_day']:5.1f} {m['flips']:4d}/{m['stops']:4d}/{m['targets']:4d}/{m['times']:4d}")


HEADER = (f"{'variant':38s} {'n':>5s} {'win%':>6s} {'avgWin':>7s} {'avgLoss':>7s} {'E[pts]':>7s} {'E[R]':>6s} "
          f"{'95% CI (E)':>16s} {'PF':>5s} {'total':>9s} {'maxDD':>7s} {'/day':>5s} flip/stop/tgt/time")


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
    ap.add_argument("--breakout", action="store_true", help="run the Donchian / opening-range grid")
    ap.add_argument("--signals", action="store_true", help="run the signal-experiment grid instead")
    ap.add_argument("--spread", type=float, default=0.5, help="index points per round trip")
    args = ap.parse_args(argv)
    bars = load_bars(args.csv)
    if len(bars) < 2000:
        print(f"WARNING: only {len(bars)} bars; results will be inconclusive", file=sys.stderr)
    run_grid(bars, BREAKOUT_GRID if args.breakout else SIGNAL_GRID if args.signals else DEFAULT_GRID, args.spread, args.train_frac)
    print("\nRead TEST rows only for any decision; a variant is credible only if its CI95 excludes 0 "
          "and it beats baseline on TEST, not just TRAIN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

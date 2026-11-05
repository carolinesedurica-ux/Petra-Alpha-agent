"""Offline backtest: slow time-series-momentum on a basket of daily bars.

Implements the specification pre-registered in docs/preregistration/trend_basket_daily.md:
  signal   sign of the L-day return of the close (default L = 120), no look-ahead
  rebalance at the last daily bar of each ISO week, hold until the next one
  sizing   weight = signal * min(cap, target_vol / sigma), sigma = 60-day stdev * sqrt(252)
  basket   each available instrument gets weight 1/N (N = instruments with enough history)
  costs    cost_bps one-way on the change in weight, plus financing % per year on gross notional
           accrued per calendar day

Standard library only. It never touches a broker or the network.

Usage:
  python backtest_trend.py --dir data/research_basket --stage dev
  python backtest_trend.py --dir data/research_basket --stage holdout   # only after dev is recorded
CSV files are named <SYMBOL>_d1.csv with columns time,open,high,low,close.
"""
from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import random
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

DAY = 86400
TRADING_DAYS = 252
# cTrader daily bars are stamped at the session OPEN, 21:00 or 22:00 UTC the previous evening, so the
# Friday session is stamped Thursday night. Adding 3 hours maps every stamp onto the intended trading date.
TRADING_DAY_SHIFT = 3 * 3600


@dataclass(frozen=True)
class Spec:
    lookback: int = 120
    vol_window: int = 60
    target_vol: float = 0.10
    max_leverage: float = 3.0
    min_history: int = 250
    cost_bps: float = 5.0
    financing_annual: float = 0.03
    long_only: bool = False


# ----------------------------------------------------------------------------- data
def _parse_time(raw: str) -> int:
    raw = raw.strip()
    try:
        v = float(raw)
        return int(v / 1000) if v > 1e11 else int(v)
    except ValueError:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())


def load_daily(path: str) -> list[tuple[int, float]]:
    """(timestamp, close) sorted by time, duplicates collapsed (later row wins), non-positive closes dropped."""
    rows: dict[int, float] = {}
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        tkey = next((k for k in ("time", "timestamp", "datetime", "date") if k in reader.fieldnames), None)
        if not tkey or "close" not in reader.fieldnames:
            raise SystemExit(f"{path}: CSV needs columns time and close")
        for r in reader:
            close = float(r["close"])
            if close > 0:
                rows[_parse_time(r[tkey])] = close
    return sorted(rows.items())


def flag_big_moves(series: list[tuple[int, float]], threshold: float = 0.25) -> list[tuple[int, float]]:
    out = []
    for (_, a), (t, b) in zip(series, series[1:]):
        if abs(b / a - 1.0) > threshold:
            out.append((t, b / a - 1.0))
    return out


# ------------------------------------------------------------------------- portfolio
@dataclass
class InstrumentRun:
    symbol: str
    first_active_ts: int | None
    gross: dict[int, float]      # ts -> pre-cost, pre-financing pnl in instrument units
    cost: dict[int, float]
    fin: dict[int, float]
    turnover: float              # sum of |dw|


def trading_date(ts: int) -> date:
    """Calendar date of the trading session a daily bar belongs to (see TRADING_DAY_SHIFT)."""
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(seconds=TRADING_DAY_SHIFT)).date()


def _week_key(ts: int) -> tuple[int, int]:
    """Trading week of a bar. A Sunday-dated session belongs to the week that follows, so each week runs
    from the Sunday-evening open to the Friday close and its last bar is the Friday session."""
    d = trading_date(ts)
    if d.weekday() == 6:
        d += timedelta(days=1)
    iso = d.isocalendar()
    return iso[0], iso[1]


def _target_weight(closes: list[float], rets: list[float], t: int, spec: Spec) -> float:
    """Weight decided at the close of bar t using bars 0..t only."""
    if t < max(spec.min_history - 1, spec.lookback, spec.vol_window):
        return 0.0
    mom = closes[t] / closes[t - spec.lookback] - 1.0
    sign = 1.0 if mom > 0 else (-1.0 if mom < 0 else 0.0)
    if spec.long_only and sign < 0:
        sign = 0.0
    window = rets[t - spec.vol_window + 1: t + 1]  # rets[i] is the return of bar i
    sigma = statistics.pstdev(window) * math.sqrt(TRADING_DAYS) if len(window) > 1 else 0.0
    if sigma <= 0 or sign == 0.0:
        return 0.0
    return sign * min(spec.max_leverage, spec.target_vol / sigma)


def run_instrument(symbol: str, series: list[tuple[int, float]], spec: Spec) -> InstrumentRun:
    ts = [t for t, _ in series]
    closes = [c for _, c in series]
    n = len(closes)
    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, n)]
    gross: dict[int, float] = {}
    cost: dict[int, float] = {}
    fin: dict[int, float] = {}
    held = 0.0
    first_active: int | None = None
    turnover = 0.0
    for t in range(1, n):
        if held != 0.0:
            gross[ts[t]] = gross.get(ts[t], 0.0) + held * rets[t]
            days = (trading_date(ts[t]) - trading_date(ts[t - 1])).days
            fin[ts[t]] = fin.get(ts[t], 0.0) + abs(held) * spec.financing_annual / 365.0 * days
        is_rebalance = t == n - 1 or _week_key(ts[t + 1]) != _week_key(ts[t])
        if is_rebalance:
            new_w = _target_weight(closes, rets, t, spec)
            if first_active is None and t >= max(spec.min_history - 1, spec.lookback, spec.vol_window):
                first_active = ts[t]
            dw = abs(new_w - held)
            if dw > 0:
                cost[ts[t]] = cost.get(ts[t], 0.0) + dw * spec.cost_bps / 1e4
                turnover += dw
            held = new_w
    return InstrumentRun(symbol, first_active, gross, cost, fin, turnover)


@dataclass
class PortfolioResult:
    dates: list[int]
    net: list[float]
    gross: list[float]
    runs: dict[str, InstrumentRun]


def run_portfolio(basket: dict[str, list[tuple[int, float]]], spec: Spec) -> PortfolioResult:
    runs = {s: run_instrument(s, ser, spec) for s, ser in basket.items()}
    all_dates = sorted({t for r in runs.values() for d in (r.gross, r.cost, r.fin) for t in d})
    dates, net, gross = [], [], []
    for d in all_dates:
        active = [r for r in runs.values() if r.first_active_ts is not None and r.first_active_ts < d]
        if not active:
            continue
        k = len(active)
        g = sum(r.gross.get(d, 0.0) for r in active) / k
        c = sum(r.cost.get(d, 0.0) + r.fin.get(d, 0.0) for r in active) / k
        dates.append(d)
        gross.append(g)
        net.append(g - c)
    return PortfolioResult(dates, net, gross, runs)


# ---------------------------------------------------------------------------- stats
def sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return statistics.fmean(returns) / sd * math.sqrt(TRADING_DAYS) if sd > 0 else 0.0


def max_drawdown(returns: list[float]) -> float:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1.0 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1.0 - eq / peak)
    return mdd


def stationary_bootstrap_sharpe(returns: list[float], *, mean_block: int = 20, resamples: int = 5000,
                                seed: int = 11) -> list[float]:
    """Sharpe ratios of stationary-bootstrap resamples (Politis-Romano), sorted ascending."""
    n = len(returns)
    if n < 2:
        return []
    rng = random.Random(seed)
    p = 1.0 / mean_block
    sqrt_t = math.sqrt(TRADING_DAYS)
    out = []
    for _ in range(resamples):
        i = rng.randrange(n)
        s = s2 = 0.0
        for _ in range(n):
            x = returns[i]
            s += x
            s2 += x * x
            i = rng.randrange(n) if rng.random() < p else (i + 1) % n
        mean = s / n
        var = s2 / n - mean * mean
        out.append(mean / math.sqrt(var) * sqrt_t if var > 1e-18 else 0.0)
    out.sort()
    return out


def summarize(dates: list[int], returns: list[float]) -> dict:
    n = len(returns)
    return {
        "days": n,
        "ann_return": statistics.fmean(returns) * TRADING_DAYS if n else 0.0,
        "ann_vol": statistics.pstdev(returns) * math.sqrt(TRADING_DAYS) if n > 1 else 0.0,
        "sharpe": sharpe(returns),
        "max_dd": max_drawdown(returns),
        "total": math.prod(1.0 + r for r in returns) - 1.0 if n else 0.0,
    }


def split_boundary(basket: dict[str, list[tuple[int, float]]], dev_fraction: float = 0.75) -> int:
    """Timestamp separating development from holdout: dev_fraction of the pooled calendar span."""
    first = min(ser[0][0] for ser in basket.values())
    last = max(ser[-1][0] for ser in basket.values())
    return int(first + dev_fraction * (last - first))


def select(result: PortfolioResult, lo: int | None = None, hi: int | None = None) -> tuple[list[int], list[float], list[float]]:
    idx = [i for i, d in enumerate(result.dates) if (lo is None or d >= lo) and (hi is None or d < hi)]
    return [result.dates[i] for i in idx], [result.net[i] for i in idx], [result.gross[i] for i in idx]


def breadth(result: PortfolioResult) -> dict[str, float]:
    """Pre-cost total pnl per instrument (sum of weight x return, instrument units)."""
    return {s: sum(r.gross.values()) for s, r in result.runs.items()}


# ----------------------------------------------------------------------------- CLI
def load_basket(directory: str, symbols: list[str] | None) -> dict[str, list[tuple[int, float]]]:
    basket = {}
    for path in sorted(glob.glob(os.path.join(directory, "*_d1.csv"))):
        sym = os.path.basename(path)[: -len("_d1.csv")]
        if symbols and sym not in symbols:
            continue
        basket[sym] = load_daily(path)
    if not basket:
        raise SystemExit(f"no *_d1.csv files found in {directory}")
    return basket


def _fmt(label: str, s: dict) -> str:
    return (f"{label:34s} days={s['days']:5d} ret={s['ann_return']*100:+6.2f}%/y vol={s['ann_vol']*100:5.2f}% "
            f"Sharpe={s['sharpe']:+.2f} maxDD={s['max_dd']*100:5.1f}%")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--stage", choices=["dev", "holdout", "full"], default="dev",
                    help="dev first; run holdout once, only after the dev result is recorded")
    ap.add_argument("--resamples", type=int, default=5000)
    args = ap.parse_args(argv)

    basket = load_basket(args.dir, args.symbols)
    base = Spec()
    boundary = split_boundary(basket)
    res = run_portfolio(basket, base)
    fmt_date = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    print("Instruments:", ", ".join(f"{s}({len(b)} bars {fmt_date(b[0][0])}..{fmt_date(b[-1][0])})" for s, b in basket.items()))
    for s, b in basket.items():
        flagged = flag_big_moves(b)
        if flagged:
            print(f"  WARNING {s}: {len(flagged)} daily moves > 25%: " + ", ".join(f"{fmt_date(t)} {m*100:+.0f}%" for t, m in flagged[:5]))
    print(f"Split: development < {fmt_date(boundary)} <= holdout\n")

    if args.stage == "dev":
        d, net, gross = select(res, None, boundary)
        print(_fmt("DEV net (base costs)", summarize(d, net)))
        print(_fmt("DEV gross (pre-cost)", summarize(d, gross)))
        print(f"Criterion A (dev Sharpe >= 0.3): {'PASS' if sharpe(net) >= 0.3 else 'FAIL'}")
        print("Holdout not touched.")
        return 0

    if args.stage == "holdout":
        d, net, gross = select(res, boundary, None)
        print(_fmt("HOLDOUT net (base costs)", summarize(d, net)))
        print(_fmt("HOLDOUT gross (pre-cost)", summarize(d, gross)))
        print(f"Criterion C (holdout Sharpe > 0): {'PASS' if sharpe(net) > 0 else 'FAIL'}")
        return 0

    d, net, gross = select(res)
    dd, dnet, _ = select(res, None, boundary)
    hd, hnet, _ = select(res, boundary, None)
    print(_fmt("FULL net", summarize(d, net)))
    print(_fmt("FULL gross", summarize(d, gross)))
    boots = stationary_bootstrap_sharpe(net, resamples=args.resamples)
    lo = boots[int(0.05 * len(boots))] if boots else float("nan")
    per_inst = breadth(res)
    pos = sum(1 for v in per_inst.values() if v > 0)
    stress = run_portfolio(basket, replace(base, cost_bps=10.0, financing_annual=0.06))
    stress_sharpe = sharpe(select(stress)[1])
    crit = {
        "A dev Sharpe >= 0.3": sharpe(dnet) >= 0.3,
        "B one-sided 95% lower bound of full Sharpe > 0": lo > 0,
        "C holdout Sharpe > 0": sharpe(hnet) > 0,
        f"D pre-cost positive in >= 5 of 8 ({pos} of {len(per_inst)})": pos >= 5,
        f"E Sharpe > 0 at 10 bps / 6% financing ({stress_sharpe:+.2f})": stress_sharpe > 0,
    }
    print(f"\nFull-sample Sharpe lower bound (95% one-sided, stationary bootstrap): {lo:+.2f}")
    for k, v in crit.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("\nOVERALL:", "ALL CRITERIA PASS (worth a pre-registered forward demo test; not a go-live signal)"
          if all(crit.values()) else "NOT ALL CRITERIA PASS (stop; no re-tuning on this data)")
    print("\nSensitivity (full sample, net Sharpe):")
    for bps in (2, 5, 10, 20):
        for fin in (0.0, 0.03, 0.06):
            r = run_portfolio(basket, replace(base, cost_bps=float(bps), financing_annual=fin))
            print(f"  cost {bps:2d} bps, financing {fin*100:.0f}%: {sharpe(select(r)[1]):+.2f}")
    print("\nDescriptive (not decision-bearing):")
    for label, sp in (("lookback 60", replace(base, lookback=60)), ("lookback 250", replace(base, lookback=250)),
                      ("long-only", replace(base, long_only=True))):
        print(f"  {label:14s} net Sharpe {sharpe(select(run_portfolio(basket, sp))[1]):+.2f}")
    print("  per-instrument pre-cost pnl (instrument units):", {s: round(v, 3) for s, v in per_inst.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

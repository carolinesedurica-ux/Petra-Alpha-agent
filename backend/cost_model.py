"""Turn cost-probe CSVs into per-market trading-cost estimates.

Reads one or more files written by ctrader_cost_probe.py and prints, per symbol:
  * typical and worst measured spread (basis points of price, across runs) and the implied one-way cost,
  * swap for longs and shorts as an annual percentage of position value.

How swap numbers are interpreted (an inference, checked against internal consistency, not a broker statement):
  PERCENTAGE: the value is already an annual percentage of position value.
  PIPS:       value x pip size = price units charged per unit per day; divided by price and x 365 = annual share.
Negative means the account pays, positive means it receives.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict

CARRY_FLAG_PCT = 15.0  # |annual swap| above this almost surely embeds a futures roll, not just interest


def pip_size(pip_position: int) -> float:
    return 10.0 ** (-int(pip_position))


def swap_annual_pct(value: float, calc_type: str, pip_position: int, mid: float | None) -> float | None:
    """Swap as an annual percentage of position value (negative = pays). None if it cannot be converted."""
    if calc_type == "PERCENTAGE":
        return float(value)
    if calc_type == "PIPS":
        if not mid or mid <= 0:
            return None
        return float(value) * pip_size(pip_position) / mid * 365.0 * 100.0
    return None


def _f(x: str) -> float | None:
    return float(x) if x not in ("", None) else None


def load_rows(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with open(p, newline="") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def aggregate(rows: list[dict]) -> list[dict]:
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)
    out = []
    for symbol, rs in sorted(by_symbol.items()):
        quoted = [r for r in rs if int(r.get("samples") or 0) > 0 and _f(r.get("spread_bps_median", ""))]
        bps = [_f(r["spread_bps_median"]) for r in quoted]
        latest = max(rs, key=lambda r: r["run_utc"])
        mid_values = [_f(r["mid"]) for r in quoted if _f(r["mid"])]
        mid = statistics.median(mid_values) if mid_values else None
        pipp = int(latest["pip_position"])
        long_pct = swap_annual_pct(float(latest["swap_long"]), latest["swap_calc_type"], pipp, mid)
        short_pct = swap_annual_pct(float(latest["swap_short"]), latest["swap_calc_type"], pipp, mid)
        carry = any(v is not None and abs(v) > CARRY_FLAG_PCT for v in (long_pct, short_pct))
        out.append({
            "symbol": symbol,
            "runs_with_quotes": len(quoted),
            "spread_bps_typical": statistics.median(bps) if bps else None,
            "spread_bps_worst": max(bps) if bps else None,
            "one_way_bps": statistics.median(bps) / 2.0 if bps else None,
            "swap_calc_type": latest["swap_calc_type"],
            "swap_long_pct": long_pct,
            "swap_short_pct": short_pct,
            "triple_swap_day": latest["swap_rollover_3days"],
            "commission": int(latest.get("commission") or 0),
            "embeds_roll_carry": carry,
        })
    return out


def _fmt(v, nd=2, suffix=""):
    return "n/a" if v is None else f"{v:+.{nd}f}{suffix}" if suffix == "%" else f"{v:.{nd}f}{suffix}"


def render(summary: list[dict]) -> str:
    head = ("| symbol | runs | spread bps (typ / worst) | one-way bps | swap long %/y | swap short %/y | calc | 3x day | roll carry? |\n"
            "|---|---:|---:|---:|---:|---:|---|---|---|")
    lines = [head]
    for s in summary:
        lines.append(f"| {s['symbol']} | {s['runs_with_quotes']} | {_fmt(s['spread_bps_typical'])} / {_fmt(s['spread_bps_worst'])} "
                     f"| {_fmt(s['one_way_bps'])} | {_fmt(s['swap_long_pct'], 2, '%')} | {_fmt(s['swap_short_pct'], 2, '%')} "
                     f"| {s['swap_calc_type']} | {s['triple_swap_day']} | {'YES' if s['embeds_roll_carry'] else 'no'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    args = ap.parse_args(argv)
    print(render(aggregate(load_rows(args.csv))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

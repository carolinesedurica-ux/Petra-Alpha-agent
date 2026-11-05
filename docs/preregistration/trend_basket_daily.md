# Pre-registration: slow trend-following on a multi-market basket (daily bars)

Written 2026-11-03, **before any daily data for these markets has been looked at**. Committed to git so the commit time evidences the order. This is one fixed specification; nothing below is tuned.

## Why this and not another intraday strategy
Every intraday variant tested so far (5-minute bars, US500) lost after costs on 3.2 years of data. At that frequency the spread is large relative to the moves. At daily/weekly frequency the cost per trade is small relative to the moves, and trend-following (time-series momentum) across several uncorrelated markets is one of the better-documented systematic approaches. It may still fail on this broker's CFDs once swap/financing costs are included. This pre-registration is built so that failure is visible.

## Markets (from `petra_research_basket.csv`)
Primary (8): XAUUSD, XAGUSD, SpotCrude, NatGas, US500, NAS100, GER40, UK100.
Alternates (US30, SpotBrent) are used only as substitutes for a primary symbol that has fewer than 5 years of daily history, decided from data availability before any return is computed. Otherwise they are descriptive only.
Source: Pepperstone cTrader demo feed via `backend/ctrader_history_export.py` (`PETRA_HISTORY_PERIOD=D1`), as much history as the broker provides.

## Primary specification P (fixed)
- Signal: sign of the 120-trading-day return of the daily close, `close[t] / close[t-120] - 1`. Zero return means flat.
- Rebalance: at the close of the last daily bar of each ISO week; the position is held until the next weekly rebalance. A signal uses only bars up to and including the rebalance bar (no look-ahead).
- Sizing: per-instrument weight = signal x min(3, 10% / sigma), where sigma = standard deviation of the last 60 daily close-to-close returns x sqrt(252). The portfolio gives each available instrument weight 1/N (N = instruments with at least 250 bars of history at that date).
- Costs (base case): 5 bps one-way on the change in notional weight at each rebalance, plus financing of 3% per year on gross notional, accrued per calendar day while a position is held. Sensitivities: costs 2 / 10 / 20 bps, financing 0% / 6%. (Broker swap rates are not available to me; the base case is a stated assumption, not a fact.)
- Returns are close-to-close on the broker's daily bars.

## Data hygiene (fixed before data arrives)
Sort, drop duplicate timestamps, drop bars with non-positive prices. Daily moves larger than 25% are flagged and reported but kept (spot energy CFDs can contain roll adjustments; if one instrument is dominated by such jumps this will be reported, not silently fixed).

## Split
- Development = first 75% of the pooled calendar span (earliest bar to last bar). Holdout = last 25%.
- Order of work: run P on the development period and record the result in a commit; only then run the holdout, once.
- Overlap disclosure: for US500, 5-minute prices from Aug 2023 were seen earlier in connection with a different (intraday) strategy. That says nothing about this daily rule, but the overlap is stated.

## Pass criteria (all must hold; evaluated net of base-case costs)
A. Development period: annualised Sharpe >= 0.3.
B. Full sample: one-sided 95% lower bound of the Sharpe ratio > 0 (stationary block bootstrap, mean block 20 days, 5,000 resamples, seed 11).
C. Holdout: Sharpe > 0.
D. Breadth: pre-cost total return positive in at least 5 of the 8 instruments over the full sample.
E. Robustness: Sharpe still > 0 at 10 bps costs and 6% financing.

## What passing and failing mean
- All pass: the strategy is worth a pre-registered forward demo test (new data, fixed rules). It is **not** evidence for going live by itself.
- Any fails: stop. No re-tuning of lookback, rebalance period, sizing or costs on this data, and no choosing a subset of markets afterwards. A new idea would need a new pre-registration and, ideally, new data.

## Power (stated up front)
With about 10 years of data the standard error of an annual Sharpe is roughly 0.3 to 0.35. Criterion B needs an observed Sharpe of roughly 0.55 or more. If the true net Sharpe is 0.5, the chance of passing B is about 50%; if it is 0.3, about 20%. A pass is informative; a fail is only mild evidence that a small edge does not exist.

## Reported but not decision-bearing
Lookbacks 60 and 250; long-only variant; per-instrument table; yearly returns; maximum drawdown; turnover; the alternates; gross vs net.

## Integrity
Run once per stage (development, then holdout). If a bug is found, both the original and corrected results are reported with what changed.

---

## Clarification made after the data arrived and before any return was computed (2026-11-03)

This changes no rule; it fixes how "the last daily bar of each ISO week" is read for this broker's data format. It was prompted only by inspecting file timestamps, not by any performance number.

- The broker's daily bars are stamped at the session **open** (21:00 or 22:00 UTC the previous evening), so the Friday session is stamped Thursday night and Monday's session is stamped Sunday night. Grouping by the raw stamp would have made the Monday session the last bar of a "week" and rebalanced on Mondays instead of Fridays.
- Convention used: trading date = bar stamp + 3 hours. A Sunday-dated session (present in some 2011 to 2013 data) belongs to the following week. Each week therefore runs from the Sunday-evening open to the Friday close, and rebalancing happens at the Friday session close, as intended.
- Financing accrues per calendar day between consecutive trading dates (a Friday to Monday gap accrues 3 days).
- Data checks run before any return was computed: all 8 files have clean headers, sorted unique timestamps, no OHLC violations and no non-positive prices; history is 12.1 to 16.5 years per market (all above the 5-year threshold, so no alternates are used). Daily moves above 25% (flagged and kept, per the hygiene rule): SpotCrude 2020-03-08 (-27%) and 2020-04-20 (-35%), NatGas 2026-01-20 (+28%), XAGUSD 2026-01-29 (-26%).
- Data fingerprint (sha256, first 12 characters): GER40 0111c2481fb8, NAS100 304c0e7a3dbb, NatGas d0d2b07c388b, SpotCrude be5f9bc129f5, UK100 a7fd19753da0, US500 07388d6abfc3, XAGUSD f21c0f7c8eec, XAUUSD 00012bb88aff. Source commit: 77d9677.

---

## Result: development stage (appended after the run; everything above was committed before it)

Run once on 2026-11-03 with code commit ebe0976 and the data fingerprints above. Development period = 2010-05 to 2022-09-18 (3,072 portfolio days, about 12.2 years), 8 markets, base costs (5 bps one-way, 3% per year financing on gross notional).

| | Result |
|---|---|
| Net annual return | +0.03% per year |
| Annualised volatility | 6.26% |
| **Net Sharpe** | **+0.01** |
| Gross (pre-cost) return / Sharpe | +1.98% per year / +0.32 |
| Maximum drawdown (net) | 23.8% |

Where the gross return went (annualised, development period): transaction costs 0.30%, financing 1.65%.

**Criterion A (development Sharpe >= 0.3): FAIL.** Under the pre-registered rule any failed criterion means stop.

Consequences, as pre-registered:
- The overall outcome is **FAIL**. The holdout (2022-09-19 onward) was **not** run and remains untouched, because the result cannot change a failed overall outcome.
- No re-tuning of lookback, rebalance period, sizing or cost assumptions on this data, and no choosing a subset of markets afterwards.
- Descriptive note (does not change the verdict): even before costs the Sharpe is only 0.32. Over 12.2 years the standard error of an annual Sharpe is roughly 0.29, so the gross result is not distinguishable from zero. The financing figure is an assumption (the broker's real swap rates were not available); a different rate would move the net number, but it could not turn a gross Sharpe of 0.32 into convincing evidence.
- This test covered one fixed specification. It says nothing about other lookbacks, other markets or other approaches, none of which were tried.

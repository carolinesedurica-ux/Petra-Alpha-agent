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

# Pre-registration: 15-minute opening-range breakout, 1.6x target (single hypothesis)

Written 2026-11-03, before the test below was run. Committed to git so the commit time evidences the order.

## Hypothesis
On US500 M5 bars from the Pepperstone cTrader demo feed, a 15-minute opening-range breakout has a positive mean P&L per trade after a 0.5 index-point round-trip spread.

## Exact specification (code: `backend/backtest_paper.py`, `Variant(signal="orb", or_minutes=15, orb_rr=1.6)`, repo commit 867ae85)
- Opening range = high/low of the first 15 minutes after 09:30 America/New_York (DST-aware).
- Enter at the first M5 close beyond the range (BUY above the high, SELL below the low) after the range completes and no later than 11:30 ET. One trade per day.
- Stop = opposite side of the range (floored at 0.08% of price). Target = 1.6 x stop distance. Forced flat at the first bar close at or after 15:55 ET.
- Stops and targets replayed on M5 highs and lows; a bar touching both counts as a stop; gaps fill at the bar open.
- Cost: 0.5 index point per round trip.
- No parameter is changed after seeing results.

## Why this hypothesis
It was the only variant of 37 with a positive average over 2.7 years of earlier data (688 trades, +2.06 pts/trade, positive in all four sub-periods), but it failed the pre-registered 99% interval rule (99% CI -0.45 to +4.55; 95% CI +0.21 to +3.88). That earlier data is what picked this variant, so it cannot also confirm it (winner's curse).

## Test window (never run for any ORB variant)
Trades opened on US trading days 2026-08-19 through 2026-11-02 inclusive (first day's open is 2026-08-19 13:30 UTC; last complete session is 2 Nov). Bars up to 2026-11-03 08:45 UTC exist; 3 Nov is excluded as incomplete. Earlier bars are used only as warm-up. Data file fingerprint: `bars_3y.csv` sha256 prefix `c48ce9980422a585` (227,621 bars merged from the seven exported slices). Expected: about 50 to 54 trades.

## Primary outcome and decision rule
Mean points per trade after costs, with a one-sided 95% bootstrap lower bound (10,000 resamples, seed 11).
- **INVALID**: fewer than 40 trades. Fix the data or code problem, document it, re-run, report both runs.
- **SUPPORTED**: at least 40 trades and the lower bound is above 0.
- **INCONCLUSIVE, FAVOURABLE**: at least 40 trades, mean above 0, lower bound at or below 0.
- **NOT SUPPORTED**: at least 40 trades and mean at or below 0.

## Power (stated up front)
Trade SD is about 25 points, so with about 52 trades the standard error is about 3.4 points. The lower bound exceeds 0 only if the sample mean exceeds roughly +5.7. If the true edge is the +2.06 seen earlier, the chance of SUPPORTED is only about 15%, and the chance of NOT SUPPORTED is about 27%. This test can mostly say "inconclusive". It cannot confirm an edge. To exclude zero at 99% for a true +2 points needs about 960 trades, roughly 4 years at one trade per day.

## What each outcome allows
- No outcome authorises live trading.
- SUPPORTED: keep collecting forward data under this same specification until the evidence reaches the target below.
- INCONCLUSIVE, FAVOURABLE: same, with no change in confidence.
- NOT SUPPORTED: drop this specific variant unless there is a new, independent rationale; do not re-tune it.
- Forward target for any live-capital decision: at least 500 additional out-of-sample trades with the lower bound of a 95% interval above 0 after a 1.0-point spread.

## Reported but not decision-bearing
Long/short split, monthly breakdown, exit reasons (stop, target, time exit), result at 1.0-point spread, the same statistic pooled with the earlier 78 trades from 28 Apr to 18 Aug 2026 (also already seen), and the pooled figure including the earlier 688 trades (selection-biased, shown for context only).

## Integrity
Run once. If a code or data bug is found, report both the original and the corrected result and say what changed.

---

## Result (appended after the run; everything above this line was committed in 72a5fbf before it)

Run once on 2026-11-03 with the specification above, code commit 867ae85, data fingerprint `c48ce9980422a585`.

**Pre-registered outcome: NOT SUPPORTED.**

| | |
|---|---|
| Window | trades opened 2026-08-19 to 2026-11-02 |
| Trades | 53 (in the expected 50 to 54 range) |
| Mean P&L per trade after 0.5 pt spread | **-3.74 pts** (SD 21.0, SE 2.88) |
| One-sided 95% lower bound | -8.26 |
| E[R] | -0.142 |
| Win rate / profit factor | 39.6% / 0.68 |
| Total / max drawdown | -198 pts / 269 pts |
| Exits | 27 stop, 13 target, 13 time exit |
| Long / short | n=22, -3.09 / n=31, -4.20 |
| At 1.0 pt spread | mean -4.24, lower bound -8.76 |

Context (not decision-bearing):
- Earlier 28 Apr to 18 Aug 2026 trades (already seen): n=78, mean -2.54. Pooled with this window: n=131, mean -3.02, 95% CI [-7.22, +1.30].
- All 819 trades pooled, including the 688 that selected this variant: mean +1.25. This figure is selection-biased and shown only for context.
- The earlier +2.06 pts/trade (n=688) and the later -3.02 pts/trade (n=131) differ by about 2 standard errors. The earlier edge did not persist in the most recent six months; it may have been a regime effect or chance.

Consequence under the pre-registered rule: this specific variant is dropped unless there is a new, independent rationale. It is not to be re-tuned on this data. No result here authorises live trading.

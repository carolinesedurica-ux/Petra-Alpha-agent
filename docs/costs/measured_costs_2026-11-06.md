# Measured trading costs, cTrader demo (Pepperstone), 5 to 6 Nov 2026

Source: read-only quote and symbol-spec probe (`backend/ctrader_cost_probe.py`), two 120-second samples:
US session (2026-11-05 14:48 UTC, `cost_probe_us.csv`) and Asian night (2026-11-06 02:03 UTC, `cost_probe_asia.csv`).
A London-open sample was attempted on 2026-11-06 and **failed** because the cTrader demo access token had expired (`CH_ACCESS_TOKEN_INVALID`), so London is **not covered**.
Generated with `python backend/cost_model.py cost_probe_us.csv cost_probe_asia.csv`.

| symbol | runs | spread bps (typ / worst) | one-way bps | swap long %/y | swap short %/y | calc | 3x day | roll carry? |
|---|---:|---:|---:|---:|---:|---|---|---|
| GER40 | 2 | 0.55 / 0.76 | 0.28 | -4.95% | -0.06% | PERCENTAGE | FRIDAY | no |
| NAS100 | 2 | 0.40 / 0.41 | 0.20 | -6.41% | +1.41% | PERCENTAGE | FRIDAY | no |
| NatGas | 2 | 11.10 / 11.16 | 5.55 | -190.47% | +168.86% | PIPS | FRIDAY | YES |
| SpotBrent | 2 | 3.44 / 3.69 | 1.72 | +49.05% | -53.57% | PIPS | FRIDAY | YES |
| SpotCrude | 2 | 2.85 / 3.05 | 1.43 | +13.35% | -16.56% | PIPS | FRIDAY | YES |
| UK100 | 2 | 1.49 / 1.87 | 0.74 | -6.24% | +1.24% | PERCENTAGE | FRIDAY | no |
| US30 | 2 | 0.40 / 0.41 | 0.20 | -6.41% | +1.41% | PERCENTAGE | FRIDAY | no |
| US500 | 2 | 0.53 / 0.54 | 0.27 | -6.41% | +1.41% | PERCENTAGE | FRIDAY | no |
| XAGUSD | 2 | 5.77 / 5.79 | 2.88 | -9.54% | +2.81% | PIPS | WEDNESDAY | no |
| XAUUSD | 2 | 0.48 / 0.49 | 0.24 | -7.18% | +2.37% | PIPS | WEDNESDAY | no |

Columns: spread in basis points of price for a full round trip (typical = median across samples, worst = largest sample median); one-way cost = typical / 2; swap as an annual percentage of position value (negative = the account pays, positive = it receives).

## What is established
- Commission is 0 on every symbol in this demo account type; costs are spread plus swap.
- US index (US500, NAS100, US30) and gold spreads were essentially identical at both sample times: they look fixed. European indices (GER40, UK100) were about 2x wider at Asian night; oil about 15% wider.
- Swap changes slightly from day to day (e.g. US500 long -6.48% on 5 Nov, -6.41% on 6 Nov).

## What is inferred, not verified
- Swap units. PERCENTAGE is read as an annual percentage; PIPS is converted as pips x pip size / price x 365. This is consistent across markets (the US indices share one pair that fits a benchmark rate of about 3.9 to 4.0% plus a 2.5% markup; GER40 and UK100 fit the same pattern with their own benchmark rates), but it has not been checked against a broker statement or the platform's symbol-info display.
- Energy swaps (SpotCrude, SpotBrent, NatGas) are far too large to be interest and almost certainly embed the futures roll (carry). They change with the futures curve, so a single snapshot cannot be applied to historical data.

## Limits
- Two 2-minute windows, not a distribution. No London open, no news events, no weekends.
- Demo-account figures. A live account may have different spreads and swaps, and a raw-spread account type would add commission.
- Historical financing differs from today's: swap tracks a benchmark rate plus markup, and benchmark rates were near zero for most of 2010 to 2021.

# Petra cTrader / Pepperstone adapter

This branch adds the first broker-agnostic step away from an Alpaca-only live pilot.
The existing Alpaca paper and micro-live code is preserved unchanged.

## What is implemented

- Strict cTrader configuration with a whitelisted `ctidTraderAccountId`.
- Demo/live endpoint separation.
- A read-only Open API smoke test that authenticates the cTrader application and
  the expected account, reads account balance, and reconciles current positions and
  pending orders.
- A manual GitHub Actions workflow for the smoke test.
- Fail-closed live arming fields reserved for the execution adapter.

The smoke test **cannot place an order**. `ALLOW_LIVE_TRADING` must remain `false`.

## Why the build starts read-only

cTrader instruments and minimum/step volumes are broker-specific. Petra must first
connect to the actual Pepperstone cTrader account and inspect its symbol metadata
before the execution layer can calculate a safe micro position for a USD 20 pilot.
Hard-coding a forex/CFD lot size before inspecting the account would defeat Petra's
risk controls.

## cTrader application setup

1. Create or use a Pepperstone account with cTrader enabled.
2. Sign in with the cTrader ID attached to that account.
3. Register a cTrader Open API application in the cTrader Open API portal.
4. Authorize the application for the intended account and obtain an access token.
5. Capture the numeric `ctidTraderAccountId` returned for that account.
6. Add these GitHub Environment secrets under `ctrader-pilot`:
   - `CTRADER_CLIENT_ID`
   - `CTRADER_CLIENT_SECRET`
   - `CTRADER_ACCESS_TOKEN`
   - `CTRADER_EXPECTED_ACCOUNT_ID`
7. Run **Petra cTrader read-only smoke** from GitHub Actions with `host=demo` first.
8. Only after the demo link passes, run the same read-only smoke against `host=live`.

## Safety gates reserved for execution phase

The later funded adapter must require all of the following simultaneously:

- `PETRA_CTRADER_ENV=live`
- `PETRA_CTRADER_DRY_RUN=false`
- `PETRA_CTRADER_PILOT_ARMED=true`
- `PETRA_CTRADER_EXECUTION_CONFIRM=CTRADER_LIVE_PILOT`
- `ALLOW_LIVE_TRADING=false`

The execution adapter should additionally fetch symbol metadata and enforce broker
minimum volume, step volume, expected margin, one managed position, a USD-denominated
daily loss cap, a hard allocation cap for the USD 20 pilot, deterministic order labels,
and reconciliation before every order.

## Funding

Google Pay funding is a broker/client-area function and does not require code inside
Petra. Funding should happen only after the account is verified, the read-only smoke
passes, and the execution adapter's symbol/risk checks are complete.

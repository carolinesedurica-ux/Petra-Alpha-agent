# Petra Production Readiness Runbook

Petra began as an Alpaca hackathon project. This runbook defines the path from demo code to a controlled personal trading system.

> **Current state:** paper-validation only. Funded-account order submission is intentionally locked.

## 1. What Petra does

Petra evaluates a small liquid US equity/ETF universe and proposes defined-risk options credit spreads. An LLM supplies a market/regime opinion, while deterministic code selects strikes, sizes positions, applies liquidity/risk gates, routes multi-leg orders, manages exits, and reconciles positions.

This architecture can automate execution, but it does **not** establish that the strategy has a profitable edge. Mock fills, synthetic demo history, and unit tests are not evidence of future profitability.

## 2. Security controls added after the hackathon

The production-hardening branch adds these controls:

- All account and trading API routes require a private `PETRA_OPERATOR_TOKEN`.
- The browser receives the token only at runtime and stores it in `sessionStorage`; it is not compiled into the React bundle.
- The scheduler route uses a separate `CRON_SECRET` and fails closed in Alpaca-backed mode when the secret is absent.
- The unprefixed duplicate FastAPI router was removed so root-level copies of trading endpoints cannot bypass `/api` protection.
- Manual option execution must pass the deterministic risk gate.
- Alpaca-backed mode can no longer invent a simulated fill when a real/paper order fails.
- Manual equity trading is disabled by default.
- Funded-account order submission is locked unless `ALLOW_LIVE_TRADING=true` **and** the connected Alpaca account number exactly matches `ALPACA_EXPECTED_ACCOUNT_NUMBER`.
- Paper-account trading does not require the funded-account unlock.

## 3. Phase A — continuous Alpaca paper trading

Use a real Alpaca **paper** account and real market/options data while keeping real-money trading locked.

Recommended production environment:

```env
ALPACA_MODE=live
ALPACA_TRADING_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
ALPACA_API_KEY=<paper key>
ALPACA_SECRET_KEY=<paper secret>

PETRA_OPERATOR_TOKEN=<long random operator secret>
CRON_SECRET=<different long random scheduler secret>

ALLOW_LIVE_TRADING=false
ALPACA_EXPECTED_ACCOUNT_NUMBER=
ENABLE_MANUAL_EQUITY_TRADING=false

MONGO_URL=<persistent MongoDB Atlas URI>
DB_NAME=petra
TICK_MAX_CANDIDATES=1
```

Use persistent MongoDB for paper validation. Do not evaluate strategy performance from serverless `/tmp` cache or synthetic demo history.

GitHub Actions also needs:

- `PETRA_URL` = the deployed Petra URL, with no trailing slash.
- `CRON_SECRET` = exactly the same scheduler secret configured in the deployment.

The scheduler should then call `/api/agent/tick` every 15 minutes on weekdays; Petra still checks Alpaca's market clock before a cycle.

## 4. Paper-validation evidence

Do not use the mock account or generated demo curve as profitability evidence. Collect real paper fills and review:

- completed trades, win/loss distribution and average gain/loss;
- net expectancy per trade;
- max drawdown and worst day/week;
- rejected orders and unfilled multi-leg orders;
- actual fill credit versus requested/mid credit;
- stop, take-profit and time-exit behavior;
- reconciliation errors, orphan legs, expiry/assignment handling;
- performance versus the SPY benchmark over the same period;
- performance by underlying, strategy and market regime.

A useful minimum validation window is at least 30 trading days and enough closed trades to expose more than one market regime. A larger sample is preferable; passing a minimum window is not proof that the strategy will remain profitable.

## 5. Phase B — funded-account prerequisites

Before changing the trading URL away from Alpaca paper:

1. Confirm the Alpaca account is eligible for the options strategies Petra uses and that required options permissions are active.
2. Confirm the production dashboard is operator-authenticated and the scheduler secret is working.
3. Confirm persistent database backups and audit logs.
4. Confirm no unresolved reconciliation or exit-management defects from paper trading.
5. Choose a starting capital amount and live risk limits deliberately; do not copy the hackathon `$100k` paper-account assumptions.
6. Rotate paper keys/secrets if they have ever been pasted into logs, screenshots, chat, source code, or build output.

## 6. Phase C — deliberate funded-account unlock

Funded orders remain blocked until all of these are true:

```env
ALPACA_TRADING_URL=https://api.alpaca.markets
ALLOW_LIVE_TRADING=true
ALPACA_EXPECTED_ACCOUNT_NUMBER=<exact funded account number>
```

The account-number whitelist is checked again immediately before order submission.

For an initial live period, use materially smaller risk than the hackathon defaults and increase only after live fills, slippage, exits and reconciliation have behaved as expected. Paper results can differ substantially from funded-market execution.

## 7. Emergency controls

- **Pause Petra** in the dashboard to prevent new autonomous cycles.
- Keep `ALLOW_LIVE_TRADING=false` whenever funded trading is not intentionally armed.
- If the deployment or credentials may be compromised, revoke/rotate Alpaca API keys and both Petra secrets.
- Manage/close positions directly in the Alpaca account if Petra is unavailable; reconciliation should detect externally closed positions on the next healthy cycle.
- Do not rely on the dashboard as the only record of positions or account state.

## 8. Deployment rule

Changes reach funded trading only after:

1. CI backend compile/tests pass;
2. frontend production build passes;
3. the change is reviewed in a pull request;
4. paper behavior is rechecked after deployment;
5. funded-account unlock remains an explicit separate deployment decision.

No code deployment should silently change `ALLOW_LIVE_TRADING` from false to true.

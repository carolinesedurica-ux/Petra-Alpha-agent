# Petra — Options Alpha Agent (PRD)

## Original problem statement
Autonomous AI options-trading agent that trades defined-risk credit spreads on a $100k Alpaca
paper account. LLM reasoning layer (Claude Sonnet 4.6) gated by a deterministic risk engine.
Optimized for P&L + a clean write-up/demo for hackathon judges. FastAPI backend + React dashboard.

## User choices
- LLM: **Claude Sonnet 4.6** (Emergent Universal Key)
- Alpaca: **LIVE paper via REST** (ALPACA_MODE=live, keys in backend/.env; MockAlpaca still available via ALPACA_MODE=mock). User chose REST-over-Python, real IEX + indicative options data, no dry-run.
- Risk: **Balanced** (2% risk/trade, max 5 concurrent, 18% min credit-to-width, Δ~0.22, 3 DTE)
- Include **MCP-style "Ask Petra"** chat panel

## Architecture
- **Backend (FastAPI, /app/backend)**: `server.py` (routes), `agent.py` (cycle orchestration,
  position mgmt, market hours, demo seed), `engines.py` (deterministic strike/size engine +
  risk gate), `llm.py` (Claude JSON signal + fallback + streaming chat), `alpaca.py` (LiveAlpaca REST + MockAlpaca, same interface: ensure_seed/market_open/get_market/advance_market/load_chain/find_strike_by_delta/build_chain_leg/expiry_ts/close_values/place_mleg/close_mleg/recompute_equity; make_alpaca factory), `pricing.py` (Black-Scholes),
  `models.py`, `database.py`, `cron_agent.py` (autonomous loop entrypoint).
- **Frontend (React, /app/frontend/src)**: single dashboard `App.js` with components:
  HeaderTerminal, MetricsRibbon, EquityChart, PositionsTable, AgentReasoningPanel, AskAgentChat,
  RiskConfigModal, SpreadPayoffModal, TradeHistoryTable. react-query polling; sonner toasts.
- **DB (MongoDB)**: account, market, positions, decisions, pnl_snapshots, config, agent_state.

## The AI logic split (as designed)
1. LLM (Claude) → structured JSON verdict {regime, direction, confidence, chosen_strategy, rationale}.
2. Deterministic strike/size engine → concrete legs (delta-targeted short strike, width, DTE, contracts).
3. Deterministic risk gate (hard stops) → max risk/trade, max concurrent, portfolio cap, min
   credit/width, bid/ask liquidity, open interest, duplicate/idempotency.
4. Execution → mock mleg order; TP 50% / stop 2x / time-exit position management.
5. Every decision (incl. rejections + LLM reasoning) logged to MongoDB and shown on the dashboard.

## Implemented (2026-06)
- Full agent cycle (manual "Run Agent Cycle" + cron_agent.py), market-hours no-op, demo seeding.
- Live Claude verdicts with schema validation + deterministic fallback on malformed output.
- Deterministic strike engine + 7-check risk gate with visible pass/fail telemetry.
- Dashboard: metrics ribbon, equity curve, positions desk w/ payoff diagrams, reasoning feed,
  risk-gate audit tab, trade history, risk-config presets (conservative/balanced/aggressive),
  streaming "Ask Petra" chat. Tested end-to-end: backend 100%, frontend 100%.

- Hackathon README.md written at /app/README.md (architecture, AI/code split, gate rules, mock→live steps).

- 2026-09-02 LIVE Alpaca paper wired: real account (PA39X74UN8VF), IEX snapshots, real chain (contracts
  + indicative snapshots w/ greeks, IV, OI), mleg limit-credit orders (negative limit = credit), 15s fill
  wait then cancel (position persisted only on fill), closes via mleg (limit for TP, market for stop/time/manual),
  marks from live option quotes (BS fallback), in-process autonomous loop every AGENT_CYCLE_SECONDS gated by
  Alpaca /clock, mode-switch wipes mock data. Day P&L = equity - Alpaca last_equity (auto daily reset in live).
  Tested: iteration_2 backend 14/14 + frontend smoke pass.

- 2026-09-02 (2): Position reconciliation vs Alpaca /positions (expired/assigned/external_close rows auto-closed,
  partial-leg + orphan warnings in decision log, throttled 60s, runs on every mark), live preset tuning
  (Balanced OI≥150 / bid-ask≤20% / credit-width≥15%; Conservative 300/12%/18%; Aggressive 50/30%/12%),
  SPY buy-and-hold benchmark line + edge readout on the equity chart (snapshots store spy; /api/pnl adds
  benchmark), Order Blotter tab (db.orders via log_order for every open/close mleg; GET /api/orders),
  fixed left-column overflow (chart flex-1). Tested: iteration_3 backend 19/19 + frontend pass.

## Backlog / next
- P1: Optional MCP server layer for external "talk to the agent".
- P2: Reconcile realized P&L for assigned/external closes uses last mark (estimate) — could pull Alpaca account activities for exact fills.
- P2: Optional news/sentiment feed for the LLM prompt (currently 'no news feed wired' in live).
- P2: Per-underlying IV term structure & earnings calendar gate.

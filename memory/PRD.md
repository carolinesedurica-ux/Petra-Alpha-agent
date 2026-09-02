# Petra — Options Alpha Agent (PRD)

## Original problem statement
Autonomous AI options-trading agent that trades defined-risk credit spreads on a $100k Alpaca
paper account. LLM reasoning layer (Claude Sonnet 4.6) gated by a deterministic risk engine.
Optimized for P&L + a clean write-up/demo for hackathon judges. FastAPI backend + React dashboard.

## User choices
- LLM: **Claude Sonnet 4.6** (Emergent Universal Key)
- Alpaca: **MOCK layer** now (real CLI wrapper stubbed; keys swap in via ALPACA_MODE=cli)
- Risk: **Balanced** (2% risk/trade, max 5 concurrent, 18% min credit-to-width, Δ~0.22, 3 DTE)
- Include **MCP-style "Ask Petra"** chat panel

## Architecture
- **Backend (FastAPI, /app/backend)**: `server.py` (routes), `agent.py` (cycle orchestration,
  position mgmt, market hours, demo seed), `engines.py` (deterministic strike/size engine +
  risk gate), `llm.py` (Claude JSON signal + fallback + streaming chat), `alpaca.py` (MockAlpaca:
  account/market/options-chain/mleg fills + CLI wrapper stub), `pricing.py` (Black-Scholes),
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

## Backlog / next
- P1: Wire real Alpaca CLI (needs user paper keys) + `--dry-run` validation path.
- P1: Optional MCP server layer for external "talk to the agent".
- P2: Reset day_start_equity on new trading day; SPY benchmark overlay on equity chart.
- P2: Per-underlying IV term structure & earnings calendar gate.

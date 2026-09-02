# Petra — Autonomous Options Alpha Agent

> Alpaca AI Trading Hackathon submission. An autonomous agent that sells **defined-risk credit spreads**
> (put credit spreads, call credit spreads, iron condors) on a $100k Alpaca paper account.
> Claude Sonnet 4.6 decides *what* to trade; deterministic code decides *whether* and *how much*.

**Stack:** FastAPI · MongoDB · React · Claude Sonnet 4.6 · Alpaca (mock layer today, CLI `mleg` wrapper ready)

---

## 1. Why this design

Options selling blows up accounts through sizing and liquidity mistakes, not through bad directional calls.
So Petra never lets the LLM touch a number that can lose money:

| Layer | Owner | Responsibility |
|---|---|---|
| **Signal** | Claude Sonnet 4.6 | Regime read, direction, confidence, strategy choice, written rationale |
| **Strike / size engine** | Deterministic code (`engines.build_spread`) | Delta-targeted short strike, width, DTE, contract count from a fixed % risk budget |
| **Risk gate** | Deterministic code (`engines.risk_gate`) | 7 hard stops — any failure = no order, ever |
| **Execution** | `alpaca.place_mleg` | Single multi-leg limit order at net credit |
| **Position mgmt** | `agent.manage_positions` | 50% take-profit, 2x-credit stop, time exit before expiry |

The LLM output is a strict JSON schema. Malformed or low-confidence output falls back to a deterministic
"no trade" verdict. Every decision — including rejections — is persisted with the full reasoning and
gate telemetry, so judges can audit exactly why each trade did or did not happen.

## 2. Agent cycle (every 15 min, market hours)

```
market snapshot ──► Claude verdict (JSON) ──► build_spread() ──► risk_gate() ──► place_mleg()
                                                    │                 │
                                            None → log "skip"   fail → log "rejected" + failing checks
```

1. **Mark & manage** open positions (TP / stop / time exit) before looking for new risk.
2. **Snapshot** the universe (SPY, QQQ, IWM, AAPL, MSFT, NVDA, TSLA, META): price, IV, day change.
3. **Ask Claude** for `{regime, direction, confidence, chosen_strategy, underlying, rationale}`.
4. **Build the spread**: short strike at |Δ| ≈ 0.22, 2-strike width, 3 DTE, contracts = ⌊risk budget / max loss⌋ (cap 10).
5. **Risk gate** (all must pass):
   - Max risk per trade ≤ 2% of equity
   - Concurrent positions < 5
   - Portfolio risk ≤ 5 × single-trade cap
   - Credit / width ≥ 18%
   - Worst leg bid-ask ≤ 15% of mid
   - Min open interest ≥ 500 on every leg
   - No duplicate open position on the same underlying
6. **Execute** as one `mleg` limit order; record position, targets, cycle id.

Presets (`conservative` / `balanced` / `aggressive`) adjust these knobs from the dashboard's Risk Config modal.

## 3. Dashboard

- **Metrics ribbon** — equity, day P&L, total P&L, buying power, win rate, open risk
- **Equity curve** — P&L snapshots per cycle
- **Positions desk** — live marks, TP/stop targets, payoff diagram per spread, manual close
- **Agent reasoning feed** — Claude's rationale + risk-gate pass/fail per cycle
- **Risk gate audit** — every check, every cycle
- **Trade history** — closed trades with exit reason
- **Ask Petra** — streaming chat grounded in live account/position state (MCP-style "talk to the agent")

## 4. Project layout

```
backend/
  server.py        REST API (/api/*)
  agent.py         cycle orchestration, position management, market hours
  engines.py       deterministic strike/size engine + risk gate (no LLM)
  llm.py           Claude signal (JSON schema + fallback) and streaming chat
  alpaca.py        MockAlpaca (account, market, chain, fills) + Alpaca CLI mleg wrapper
  pricing.py       Black-Scholes price / delta
  cron_agent.py    autonomous loop entrypoint
  models.py, database.py
frontend/src/
  App.js           dashboard
  components/      HeaderTerminal, MetricsRibbon, EquityChart, PositionsTable,
                   AgentReasoningPanel, AskAgentChat, RiskConfigModal,
                   SpreadPayoffModal, TradeHistoryTable
```

## 5. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/account` | equity, cash, buying power, P&L |
| GET | `/api/positions` | open positions with live marks |
| GET | `/api/trades` | closed trade history |
| GET | `/api/decisions` | reasoning log + risk-gate telemetry |
| GET | `/api/pnl` | equity curve snapshots |
| GET | `/api/market` | universe snapshot |
| GET | `/api/status` | market hours, agent state, last cycle |
| GET / PUT | `/api/config` | risk configuration |
| POST | `/api/agent/run-cycle` | trigger one cycle now |
| POST | `/api/agent/pause` | pause / resume autonomy |
| POST | `/api/positions/{id}/close` | manual close |
| POST | `/api/chat` | streaming "Ask Petra" |

## 6. Running locally

```bash
# backend
cd backend
pip install -r requirements.txt
# .env: MONGO_URL, DB_NAME, EMERGENT_LLM_KEY, ALPACA_MODE=mock
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# frontend
cd frontend
yarn install
# .env: REACT_APP_BACKEND_URL=http://localhost:8001
yarn start

# autonomous loop (crontab, weekdays 9–16 ET every 15 min)
*/15 9-16 * * 1-5  cd /app/backend && python cron_agent.py >> /var/log/agent_cron.log 2>&1
```

## 7. Switching from mock to live Alpaca paper

The mock layer simulates the account, a random-walk market, a Black-Scholes options chain and instant
mid-price fills so the entire pipeline runs end-to-end without keys. To route real paper orders:

1. Install and authenticate the Alpaca CLI with paper-trading keys.
2. In `backend/.env` set `ALPACA_MODE=cli`, `ALPACA_API_KEY`, `ALPACA_API_SECRET`.
3. `alpaca.place_mleg` shells out to `alpaca orders create --order-class mleg ...`
   (supports `--dry-run` for validation). Account/market/chain reads remain to be wired to the
   Alpaca data API — the engine and gate are unchanged.

## 8. Safety properties (for judges)

- **LLM never sizes or executes.** It returns an opinion; code decides.
- **Defined risk only.** Every position is a spread; max loss is known at entry and enforced before order.
- **Idempotent cycles.** Duplicate underlyings are rejected; each cycle has an id stamped on every artifact.
- **Fail closed.** LLM error, schema violation, or any gate failure → no order, decision logged as rejected.
- **Full audit trail.** Prompt, verdict, gate checks, order and exit reason are all stored in MongoDB and rendered live.

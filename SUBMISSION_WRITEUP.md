# Petra — Autonomous Options Alpha Agent
### Alpaca AI Trading Hackathon — Submission Write-Up

**Team / Project:** Petra Options Alpha Agent  
**Account Number:** `PA39X74UN8VF` (Fresh Paper Account, $100,000 Starting Equity, Level 3 Options)  
**Live Stack:** FastAPI · React · Claude Sonnet 4.6 · Model Context Protocol (MCP) · Alpaca Trading & Data APIs  
**Trading Strategy:** Defined-Risk Options Credit Spreads (Put Spreads, Call Spreads, Iron Condors)

---

## 1. AI Logic & Signal Architecture

Options sellers blow up accounts through sizing, liquidity, and tail-risk errors—not slight directional inaccuracy. Therefore, Petra implements a strict **AI/Deterministic Code Separation**: the LLM acts solely as the macro/regime opinion layer, while deterministic math governs all sizing, strikes, and execution.

- **Model & Contract:** Claude Sonnet 4.6 is queried every 15 minutes during market hours with a standardized market snapshot across an 8-underlying liquid universe (`SPY`, `QQQ`, `IWM`, `AAPL`, `MSFT`, `NVDA`, `TSLA`, `META`).
- **Strict JSON Output:** The model outputs a validated JSON schema:
  ```json
  {
    "regime": "trending_up | trending_down | range_bound | high_volatility",
    "direction": "bullish | bearish | neutral",
    "confidence": 0.0 - 1.0,
    "chosen_strategy": "put_credit_spread | call_credit_spread | iron_condor",
    "rationale": "<=240 characters citing price action and IV"
  }
  ```
- **Fail-Closed Fallback:** If the LLM call times out, encounters a schema violation, or returns low confidence (<0.50), the system defaults to a deterministic rule-based regime or logs a "no trade" decision. The LLM is **never** permitted to calculate strikes, contract quantities, or place orders.

---

## 2. Deterministic Strike Engine & The 7 Hard Risk Gates

Once the signal is selected, Petra's deterministic engines take complete control:

1. **Strike Selection:** Delta-targeted short strike (|$\Delta$| $\approx$ 0.20–0.22) using Black-Scholes Greeks and live Alpaca options chains, with a fixed 2-strike width and short-term expiration (2–7 DTE) to maximize theta decay.
2. **Dynamic Position Sizing:** Position contracts are computed as $\lfloor \text{Risk Budget} / \text{Max Loss per spread} \rfloor$, capped at 10 contracts per trade.
3. **The 7 Hard Risk Gates (Every Check Must Pass):**
   - **Max Trade Risk:** Loss cap $\le$ 2.0% of total equity.
   - **Portfolio Risk Cap:** Total open spread risk $\le$ 10.0% of portfolio equity.
   - **Max Concurrent Positions:** Strictly $\le$ 5 open spreads at any time.
   - **Credit / Width Ratio:** Net credit collected must be $\ge$ 15%–18% of the spread width.
   - **Liquidity & Bid-Ask Gate:** Worst leg bid-ask spread must be $\le$ 15% of the mid-price.
   - **Open Interest Filter:** Minimum open interest $\ge$ 300 contracts on every individual leg.
   - **Idempotency & Duplicate Gate:** No concurrent positions on the same underlying symbol.
4. **Autonomous Position Management:** Open positions are monitored in real-time with automated profit-taking at **50% of credit collected**, stop-loss exit at **2x credit**, and time exit before expiration.

---

## 3. Alpaca Infrastructure & MCP Implementation

Petra interfaces with Alpaca across three complementary layers:

### A. Live Paper Trading & Data API
- **Account & Market Clock:** Real-time synchronization with `/v2/account` (day-start equity baseline for intraday P&L) and `/v2/clock` for autonomous scheduling.
- **Options Discovery:** Real-time contracts filtering (`/v2/options/contracts`) and quotes/Greeks snapshots (`/v1beta1/options/snapshots?feed=indicative`).
- **Multi-Leg (`mleg`) Execution:** Orders are placed as atomic multi-leg limit orders (`order_class: "mleg"`) at negative limit prices (net credit). Orders employ a 15-second fill window before auto-cancellation to prevent slippage.
- **Live Reconciliation Engine:** Every cycle compares internal spread records against Alpaca `/v2/positions` to identify expired legs, external closes, or assignments, ensuring zero database drift.

### B. Model Context Protocol (MCP) Integration
To meet and exceed the hackathon's MCP requirement, Petra integrates both official and custom MCP servers:
1. **Official Alpaca MCP Server (`alpaca-mcp-server` v2.3.1):** Configured via `.mcp.json` with paper credentials, enabling external LLM tools to inspect Alpaca balances, orders, and market feeds.
2. **Petra Alpha MCP Server (`petra_mcp_server.py`):** A custom FastMCP server exposing 7 specialized trading tools to any MCP client (Claude Desktop, Cursor, IDEs):
   - `get_alpaca_account`: Real-time paper equity and buying power.
   - `get_market_universe`: Live universe snapshot and market hours.
   - `get_options_chain`: Live options expirations, strikes, and IV.
   - `get_open_positions`: Live marks and TP/SL payoff monitoring.
   - `evaluate_risk_gate`: Dry-run risk checks with full audit telemetry.
   - `trigger_agent_cycle`: Execute an autonomous trading cycle.
   - `reconcile_positions`: Reconcile internal state against Alpaca positions.
3. **Status Monitoring:** Integrated into the REST API (`GET /api/mcp/status`) and displayed live on the dashboard header terminal.

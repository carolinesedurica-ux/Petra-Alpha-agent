import { useEffect, useState } from "react";
import {
  Activity, Bot, Clock3, Crosshair, Layers3, Receipt,
  RefreshCw, ShieldCheck, TrendingDown, TrendingUp, Wallet, Zap,
} from "lucide-react";
import { CTraderCandlestickChart } from "@/components/CTraderCandlestickChart";

const Tile = ({ icon: Icon, label, value, sub, tone = "text-slate-100" }) => (
  <div className="term-card p-5">
    <div className="flex items-center gap-2 text-slate-500 mb-2">
      <Icon size={14} />
      <span className="text-[10px] font-mono uppercase tracking-[0.18em]">{label}</span>
    </div>
    <div className={`text-2xl font-mono font-bold ${tone}`}>{value}</div>
    {sub && <div className="mt-1 text-xs font-mono text-slate-500">{sub}</div>}
  </div>
);

const money = (value, signed = false) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const prefix = signed && n > 0 ? "+" : "";
  return `${prefix}$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const fmtPrice = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};

const fmtClock = (seconds) => {
  if (!Number.isFinite(seconds)) return "—";
  const date = new Date(seconds * 1000);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

const actionTone = (action = "") => {
  if (action.includes("BUY")) return "text-[#00F0B5]";
  if (action.includes("SELL") || action === "CLOSE") return "text-[#FF6B8A]";
  if (action === "HOLD") return "text-[#FFB800]";
  return "text-slate-300";
};

export const CTraderReadOnlyDashboard = ({ status, snapshot, loading, error, onRefresh }) => {
  const [now, setNow] = useState(Date.now());
  const paper = status?.last_paper_cycle || {};
  const profile = paper?.paper_profile || {};
  const interval = Number(status?.paper_interval_seconds || 300);
  const position = paper?.position || null;
  const analysisDecision = paper?.analysis_decision || "WAITING";
  const finalDecision = paper?.final_decision || "WAITING";
  const lastAction = paper?.last_action || "WAITING";
  const paperReady = status?.paper_autonomous_ready === true;
  const paperStatus = paper?.status || "not_run";
  const balance = snapshot?.balance;
  const lastCycleAt = paper?.updated_at ? Date.parse(paper.updated_at) : null;
  const secondsToNext = Number.isFinite(lastCycleAt)
    ? Math.max(0, interval - Math.floor((now - lastCycleAt) / 1000))
    : null;
  const countdown = secondsToNext == null
    ? "—"
    : `${String(Math.floor(secondsToNext / 60)).padStart(2, "0")}:${String(secondsToNext % 60).padStart(2, "0")}`;

  const modelCapital = Number(profile?.starting_balance ?? paper?.balance);
  const minNotional = Number(profile?.min_order_notional_usd);
  const maxNotional = Number(profile?.max_order_notional_usd);
  const riskPct = Number(profile?.risk_pct);
  const activeNotional = Number(position?.notional_usd ?? (Number(position?.entry_price) * Number(position?.quantity)));
  const estimatedRisk = position
    ? Math.abs(Number(position.entry_price) - Number(position.stop_loss)) * Number(position.quantity)
    : NaN;

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const pnlTone = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n) || n === 0) return "text-slate-100";
    return n > 0 ? "text-[#00F0B5]" : "text-[#FF6B8A]";
  };

  return (
    <div className="min-h-screen grain bg-[#070b12] text-slate-100">
      <header className="border-b border-[var(--border)] bg-[#06090e]/95 backdrop-blur-xl">
        <div className="mx-auto max-w-[1500px] px-4 sm:px-6 py-5 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-md flex items-center justify-center border border-[var(--border-accent)] bg-[#00F0B5]/10">
              <Zap size={19} color="#00F0B5" />
            </div>
            <div>
              <h1 className="font-display text-lg sm:text-xl font-bold">
                PETRA <span className="text-[#00F0B5]">// cTRADER DEMO</span>
              </h1>
              <div className="text-[10px] font-mono uppercase tracking-[0.22em] text-slate-500 mt-1">
                PEPPERSTONE · AUTONOMOUS PAPER MODE · LIVE EXECUTION OFF
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
            <span className="term-well px-3 py-2 text-[#00F0B5]">CTRADER · {status?.ctrader_environment?.toUpperCase() || "DEMO"}</span>
            <span className="term-well px-3 py-2 text-slate-300">API ID · {status?.expected_account_id || "—"}</span>
            <span className="term-well px-3 py-2 text-[#00F0B5]">AUTONOMOUS PAPER · {paperReady ? "ON" : "WAIT"}</span>
            <span className="term-well px-3 py-2 text-[#FF3B69]">LIVE TRADING · OFF</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-4 sm:px-6 py-6 space-y-5">
        <div className="term-card p-4 flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-xs font-mono uppercase tracking-[0.2em] text-[#00F0B5]">Pepperstone cTrader connection</div>
            <div className="mt-1 text-sm text-slate-400">
              {loading
                ? "Reading the authorized demo account…"
                : error
                  ? "Snapshot unavailable"
                  : snapshot?.status === "ok"
                    ? "Demo account authenticated. Autonomous paper engine is isolated from broker execution."
                    : "Waiting for broker snapshot."}
            </div>
          </div>
          <button
            onClick={onRefresh}
            disabled={loading}
            className="term-well px-4 py-2 text-xs font-mono text-slate-200 hover:text-white disabled:opacity-50 flex items-center gap-2"
          >
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> REFRESH ACCOUNT
          </button>
        </div>

        {error && (
          <div className="term-card border border-[#FF3B69]/40 p-4 text-sm font-mono text-[#FF6B8A]">
            cTrader snapshot failed. Petra remains in paper mode and broker execution stays disabled. {error?.response?.data?.error || error?.message || ""}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <Tile icon={Wallet} label="Broker Demo Balance" value={money(balance)} sub={`Broker: ${snapshot?.broker_name || "Pepperstone"}`} />
          <Tile icon={Layers3} label="Broker Open Positions" value={snapshot?.open_positions ?? "—"} sub="Broker account remains untouched" />
          <Tile icon={Receipt} label="Broker Pending Orders" value={snapshot?.pending_orders ?? "—"} sub="No autonomous broker orders" />
          <Tile icon={ShieldCheck} label="Execution Boundary" value="PAPER ONLY" sub="Dry-run on · live trading off" tone="text-[#FFB800]" />
        </div>

        <section className="term-card border border-[#00F0B5]/30 overflow-hidden">
          <div className="p-5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[#00F0B5]">
                <Bot size={16} />
                <span className="text-xs font-mono uppercase tracking-[0.2em]">Autonomous Paper Trader</span>
              </div>
              <div className="mt-2 text-sm text-slate-400">
                Petra evaluates cTrader US500 data every {Math.round(interval / 60)} minutes and autonomously opens, holds, or closes simulated positions. No order is sent to Pepperstone.
              </div>
            </div>
            <div className="flex gap-2 flex-wrap font-mono text-xs">
              <span className="term-well px-3 py-2 text-[#00F0B5]">ENGINE · {paperStatus === "ok" ? "RUNNING" : paperStatus.toUpperCase()}</span>
              <span className="term-well px-3 py-2 text-[#FFB800]">NEXT CYCLE · {countdown}</span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-0 border-b border-[var(--border)]">
            <div className="p-5 border-b md:border-r xl:border-b-0 border-[var(--border)]">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Current signal</div>
              <div className={`mt-2 text-2xl font-mono font-bold ${analysisDecision === "BUY" ? "text-[#00F0B5]" : analysisDecision === "SELL" ? "text-[#FF6B8A]" : "text-slate-300"}`}>
                {analysisDecision}
              </div>
              <div className="text-xs font-mono text-slate-500 mt-1">Final: {finalDecision}</div>
            </div>
            <div className="p-5 border-b xl:border-b-0 xl:border-r border-[var(--border)]">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Autonomous action</div>
              <div className={`mt-2 text-2xl font-mono font-bold ${actionTone(lastAction)}`}>{lastAction}</div>
              <div className="text-xs font-mono text-slate-500 mt-1">Cycle #{paper?.cycles ?? "—"}</div>
            </div>
            <div className="p-5 border-b md:border-r xl:border-b-0 border-[var(--border)]">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Current mark</div>
              <div className="mt-2 text-2xl font-mono font-bold text-slate-100">{fmtPrice(paper?.market_price)}</div>
              <div className="text-xs font-mono text-slate-500 mt-1">M5 bar · {fmtClock(paper?.market_updated_at)}</div>
            </div>
            <div className="p-5 border-b xl:border-b-0 xl:border-r border-[var(--border)]">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Paper balance</div>
              <div className="mt-2 text-2xl font-mono font-bold text-slate-100">{money(paper?.balance)}</div>
              <div className="text-xs font-mono text-slate-500 mt-1">$100 model account</div>
            </div>
            <div className="p-5">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Paper equity</div>
              <div className={`mt-2 text-2xl font-mono font-bold ${pnlTone(Number(paper?.equity) - Number(paper?.balance))}`}>{money(paper?.equity)}</div>
              <div className="text-xs font-mono text-slate-500 mt-1">Mark-to-model equity</div>
            </div>
          </div>

          <div className="p-5 border-b border-[var(--border)] bg-[#08111a]">
            <div className="flex items-center gap-2 mb-3">
              <ShieldCheck size={14} className="text-[#00F0B5]" />
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-400">$100 Paper Risk Profile</div>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 font-mono text-sm">
              <div className="term-well p-3"><div className="text-[10px] text-slate-500">MODEL CAPITAL</div><div className="mt-1 text-slate-100 font-bold">{money(modelCapital)}</div></div>
              <div className="term-well p-3"><div className="text-[10px] text-slate-500">MIN ORDER</div><div className="mt-1 text-[#00F0B5] font-bold">{money(minNotional)}</div></div>
              <div className="term-well p-3"><div className="text-[10px] text-slate-500">MAX ORDER</div><div className="mt-1 text-slate-100 font-bold">{money(maxNotional)}</div></div>
              <div className="term-well p-3"><div className="text-[10px] text-slate-500">RISK BUDGET</div><div className="mt-1 text-[#FFB800] font-bold">{Number.isFinite(riskPct) ? `${riskPct.toFixed(2)}%` : "—"}</div></div>
              <div className="term-well p-3"><div className="text-[10px] text-slate-500">ACTIVE NOTIONAL</div><div className="mt-1 text-slate-100 font-bold">{money(activeNotional)}</div></div>
              <div className="term-well p-3"><div className="text-[10px] text-slate-500">RISK TO STOP</div><div className="mt-1 text-[#FF6B8A] font-bold">{money(estimatedRisk)}</div></div>
            </div>
            <div className="mt-3 text-[10px] font-mono text-slate-500">Petra skips a setup when the risk-safe capacity cannot support at least a $10 simulated position.</div>
          </div>

          <div className="p-5 border-b border-[var(--border)]">
            <CTraderCandlestickChart
              candles={paper?.candles || []}
              position={position}
              marketPrice={paper?.market_price}
              barPeriod={paper?.bar_period || "M5"}
              updatedAt={paper?.market_updated_at}
            />
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-12">
            <div className="p-5 xl:col-span-7 xl:border-r border-b xl:border-b-0 border-[var(--border)]">
              <div className="flex items-center gap-2 mb-4">
                <Crosshair size={14} className="text-slate-500" />
                <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Current paper position</div>
              </div>
              {position ? (
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3 font-mono text-sm">
                  <div className="term-well p-3"><div className="text-[10px] text-slate-500">SIDE</div><div className={`mt-1 font-bold ${position.side === "BUY" ? "text-[#00F0B5]" : "text-[#FF6B8A]"}`}>{position.side}</div></div>
                  <div className="term-well p-3"><div className="text-[10px] text-slate-500">SYMBOL</div><div className="mt-1 text-slate-100">{position.symbol || "US500"}</div></div>
                  <div className="term-well p-3"><div className="text-[10px] text-slate-500">ENTRY</div><div className="mt-1 text-slate-100">{fmtPrice(position.entry_price)}</div></div>
                  <div className="term-well p-3"><div className="text-[10px] text-slate-500">QUANTITY</div><div className="mt-1 text-slate-100">{position.quantity ?? "—"}</div></div>
                  <div className="term-well p-3"><div className="text-[10px] text-slate-500">STOP LOSS</div><div className="mt-1 text-[#FF6B8A]">{fmtPrice(position.stop_loss)}</div></div>
                  <div className="term-well p-3"><div className="text-[10px] text-slate-500">TAKE PROFIT</div><div className="mt-1 text-[#00F0B5]">{fmtPrice(position.take_profit)}</div></div>
                </div>
              ) : (
                <div className="term-well p-5 text-sm font-mono text-slate-500">No paper position is currently open. Petra will wait for the next actionable signal.</div>
              )}
            </div>

            <div className="p-5 xl:col-span-5">
              <div className="flex items-center gap-2 mb-4">
                <Activity size={14} className="text-slate-500" />
                <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">Performance</div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono text-sm">
                <div className="term-well p-3"><div className="text-[10px] text-slate-500">REALIZED P&L</div><div className={`mt-1 text-lg font-bold ${pnlTone(paper?.realized_pnl)}`}>{money(paper?.realized_pnl, true)}</div></div>
                <div className="term-well p-3"><div className="text-[10px] text-slate-500">UNREALIZED P&L</div><div className={`mt-1 text-lg font-bold ${pnlTone(paper?.unrealized_pnl)}`}>{money(paper?.unrealized_pnl, true)}</div></div>
                <div className="term-well p-3"><div className="text-[10px] text-slate-500">WINS</div><div className="mt-1 text-[#00F0B5] font-bold">{paper?.wins ?? 0}</div></div>
                <div className="term-well p-3"><div className="text-[10px] text-slate-500">LOSSES</div><div className="mt-1 text-[#FF6B8A] font-bold">{paper?.losses ?? 0}</div></div>
              </div>
              <div className="term-well p-3 mt-3 font-mono text-xs">
                <div className="text-slate-500">LAST CYCLE UPDATE</div>
                <div className="mt-1 text-slate-200">{paper?.updated_at ? new Date(paper.updated_at).toLocaleString() : "—"}</div>
              </div>
            </div>
          </div>
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          <div className="term-card p-4 flex items-center gap-3">
            <Clock3 size={16} className="text-[#FFB800]" />
            <div><div className="text-[10px] font-mono text-slate-500 uppercase tracking-[0.18em]">Cycle cadence</div><div className="font-mono text-sm mt-1">Every {Math.round(interval / 60)} minutes</div></div>
          </div>
          <div className="term-card p-4 flex items-center gap-3">
            {analysisDecision === "SELL" ? <TrendingDown size={16} className="text-[#FF6B8A]" /> : <TrendingUp size={16} className="text-[#00F0B5]" />}
            <div><div className="text-[10px] font-mono text-slate-500 uppercase tracking-[0.18em]">Latest model signal</div><div className={`font-mono text-sm mt-1 ${analysisDecision === "BUY" ? "text-[#00F0B5]" : analysisDecision === "SELL" ? "text-[#FF6B8A]" : "text-slate-300"}`}>{analysisDecision}</div></div>
          </div>
          <div className="term-card p-4 flex items-center gap-3">
            <ShieldCheck size={16} className="text-[#00F0B5]" />
            <div><div className="text-[10px] font-mono text-slate-500 uppercase tracking-[0.18em]">Safety state</div><div className="font-mono text-sm mt-1 text-[#00F0B5]">BROKER EXECUTION DISABLED</div></div>
          </div>
        </div>
      </main>
    </div>
  );
};
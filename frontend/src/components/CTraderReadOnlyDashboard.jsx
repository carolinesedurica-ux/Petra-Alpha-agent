import { ShieldCheck, Wallet, Layers3, Receipt, RefreshCw, Zap } from "lucide-react";

const Tile = ({ icon: Icon, label, value, sub }) => (
  <div className="term-card p-5">
    <div className="flex items-center gap-2 text-slate-500 mb-2">
      <Icon size={14} />
      <span className="text-[10px] font-mono uppercase tracking-[0.18em]">{label}</span>
    </div>
    <div className="text-2xl font-mono font-bold text-slate-100">{value}</div>
    {sub && <div className="mt-1 text-xs font-mono text-slate-500">{sub}</div>}
  </div>
);

export const CTraderReadOnlyDashboard = ({ status, snapshot, loading, error, onRefresh }) => {
  const balance = snapshot?.balance;
  const balanceText = Number.isFinite(balance) ? `$${Number(balance).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";

  return (
    <div className="min-h-screen grain bg-[#070b12] text-slate-100">
      <header className="border-b border-[var(--border)] bg-[#06090e]/95 backdrop-blur-xl">
        <div className="mx-auto max-w-[1500px] px-4 sm:px-6 py-5 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-md flex items-center justify-center border border-[var(--border-accent)] bg-[#00F0B5]/10">
              <Zap size={19} color="#00F0B5" />
            </div>
            <div>
              <h1 className="font-display text-lg sm:text-xl font-bold">PETRA <span className="text-[#00F0B5]">// cTRADER DEMO</span></h1>
              <div className="text-[10px] font-mono uppercase tracking-[0.22em] text-slate-500 mt-1">PEPPERSTONE · READ ONLY · EXECUTION DISABLED</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
            <span className="term-well px-3 py-2 text-[#00F0B5]">CTRADER · {status?.ctrader_environment?.toUpperCase() || "DEMO"}</span>
            <span className="term-well px-3 py-2 text-slate-300">API ID · {status?.expected_account_id || "—"}</span>
            <span className="term-well px-3 py-2 text-[#FFB800]">DRY RUN · ON</span>
            <span className="term-well px-3 py-2 text-[#FF3B69]">LIVE TRADING · OFF</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-4 sm:px-6 py-6 space-y-5">
        <div className="term-card p-4 flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-xs font-mono uppercase tracking-[0.2em] text-[#00F0B5]">Pepperstone cTrader connection</div>
            <div className="mt-1 text-sm text-slate-400">
              {loading ? "Reading the authorized demo account…" : error ? "Snapshot unavailable" : snapshot?.status === "ok" ? "Demo account authenticated and readable." : "Waiting for broker snapshot."}
            </div>
          </div>
          <button onClick={onRefresh} disabled={loading}
            className="term-well px-4 py-2 text-xs font-mono text-slate-200 hover:text-white disabled:opacity-50 flex items-center gap-2">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> REFRESH ACCOUNT
          </button>
        </div>

        {error && (
          <div className="term-card border border-[#FF3B69]/40 p-4 text-sm font-mono text-[#FF6B8A]">
            cTrader snapshot failed. Petra remains read-only and execution is disabled. {error?.response?.data?.error || error?.message || ""}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <Tile icon={Wallet} label="Demo Balance" value={balanceText} sub={`Broker: ${snapshot?.broker_name || "Pepperstone"}`} />
          <Tile icon={Layers3} label="Open Positions" value={snapshot?.open_positions ?? "—"} sub="Read from cTrader reconcile" />
          <Tile icon={Receipt} label="Pending Orders" value={snapshot?.pending_orders ?? "—"} sub="Read from cTrader reconcile" />
          <Tile icon={ShieldCheck} label="Execution" value="DISABLED" sub="Dry-run on · pilot unarmed" />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div className="term-card p-5">
            <div className="text-xs font-mono uppercase tracking-[0.18em] text-slate-500 mb-4">Connection details</div>
            <div className="space-y-3 font-mono text-sm">
              <div className="flex justify-between gap-4"><span className="text-slate-500">Provider</span><span className="text-slate-200">cTrader</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Environment</span><span className="text-[#00F0B5]">DEMO</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">API account ID</span><span className="text-slate-200">{snapshot?.account_id || status?.expected_account_id || "—"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Authenticated</span><span className="text-[#00F0B5]">{snapshot?.authenticated ? "YES" : "—"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Execution enabled</span><span className="text-[#FF3B69]">NO</span></div>
            </div>
          </div>

          <div className="term-card p-5">
            <div className="text-xs font-mono uppercase tracking-[0.18em] text-slate-500 mb-4">Safety gate</div>
            <div className="space-y-3 font-mono text-sm">
              <div className="flex justify-between gap-4"><span className="text-slate-500">Runtime ready</span><span className="text-[#00F0B5]">{status?.ready_for_read_only ? "YES" : "NO"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Dry run</span><span className="text-[#00F0B5]">{status?.dry_run ? "ON" : "OFF"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Pilot armed</span><span className="text-slate-200">{status?.pilot_armed ? "YES" : "NO"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Allow live trading</span><span className="text-[#FF3B69]">{status?.allow_live_trading ? "YES" : "NO"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Mode</span><span className="text-[#FFB800]">READ ONLY</span></div>
            </div>
          </div>
        </div>

        <div className="text-center text-[10px] font-mono uppercase tracking-[0.18em] text-slate-700 py-5">
          PETRA · PEPPERSTONE cTRADER DEMO · ACCOUNT TELEMETRY ONLY · NO ORDER SUBMISSION
        </div>
      </main>
    </div>
  );
};

import { motion } from "framer-motion";
import { Activity, Radio, Pause, Play, Settings2, Zap } from "lucide-react";

export const HeaderTerminal = ({ account, status, agent, onRunCycle, onPause, onOpenRisk, cycling }) => {
  const marketOpen = status?.market?.open;
  const paused = agent?.paused;

  const Badge = ({ dot, label, value, color = "#94a3b8" }) => (
    <div className="flex items-center gap-2 px-3 py-1.5 term-well">
      {dot && (
        <span className="relative flex h-2 w-2">
          <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: color }} />
        </span>
      )}
      <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">{label}</span>
      <span className="text-[11px] font-mono font-semibold" style={{ color }}>{value}</span>
    </div>
  );

  return (
    <header className="sticky top-0 z-30 border-b border-[var(--border)] bg-[#06090e]/90 backdrop-blur-xl">
      <div className="mx-auto max-w-[1600px] px-4 sm:px-6 py-3 flex flex-wrap items-center gap-3 justify-between">
        <div className="flex items-center gap-3">
          <div className="relative h-9 w-9 rounded-md flex items-center justify-center border border-[var(--border-accent)]"
               style={{ background: "rgba(0,240,181,0.1)" }}>
            <Zap size={18} color="#00F0B5" />
          </div>
          <div>
            <h1 className="font-display text-base sm:text-lg font-bold tracking-tight text-slate-100 leading-none">
              PETRA <span className="text-[#00F0B5]">// OPTIONS ALPHA</span>
            </h1>
            <p className="text-[10px] font-mono uppercase tracking-[0.22em] text-slate-500 mt-0.5">
              AUTONOMOUS AGENT // DEFINED-RISK CREDIT SPREADS
            </p>
          </div>
        </div>

        <div className="hidden lg:flex items-center gap-2 flex-wrap">
          <Badge dot label="ALPACA" value={`PAPER ${account?.mode?.toUpperCase() || "MOCK"}`} color="#FFE600" />
          <Badge label="ACCT" value={account?.account_id || "—"} />
          <Badge dot label="NYSE" value={marketOpen ? "OPEN" : "CLOSED"} color={marketOpen ? "#00F0B5" : "#FF3B69"} />
          <Badge dot label="CRON" value={paused ? "PAUSED" : `${agent?.total_cycles || 0} CYCLES`}
                 color={paused ? "#FFB800" : "#00D4FF"} />
        </div>

        <div className="flex items-center gap-2">
          <button data-testid="open-risk-settings-btn" onClick={onOpenRisk}
            className="flex items-center gap-1.5 px-3 py-2 term-well text-slate-300 hover:text-white hover:border-[var(--border-accent)] transition-colors text-xs font-mono">
            <Settings2 size={14} /> RISK
          </button>
          <button data-testid="agent-emergency-pause-btn" onClick={() => onPause(!paused)}
            className="flex items-center gap-1.5 px-3 py-2 term-well hover:border-[#FFB800] transition-colors text-xs font-mono"
            style={{ color: paused ? "#00F0B5" : "#FFB800" }}>
            {paused ? <Play size={14} /> : <Pause size={14} />} {paused ? "RESUME" : "PAUSE"}
          </button>
          <motion.button
            data-testid="agent-run-cycle-btn" onClick={onRunCycle} disabled={cycling}
            whileTap={{ scale: 0.96 }}
            className="relative flex items-center gap-2 px-4 py-2 rounded-md font-mono text-xs font-bold text-[#06090e] disabled:opacity-70"
            style={{ background: "linear-gradient(135deg,#00F0B5,#00D4FF)" }}>
            <span className="relative flex h-2 w-2">
              {cycling && <span className="radar-ping absolute inline-flex h-2 w-2 rounded-full" />}
              <span className="relative inline-flex rounded-full h-2 w-2 bg-[#06090e]" />
            </span>
            {cycling ? "RUNNING CYCLE…" : "RUN AGENT CYCLE"}
          </motion.button>
        </div>
      </div>
      {cycling && <div className="scan-line" />}
    </header>
  );
};

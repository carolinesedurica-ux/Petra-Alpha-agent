import { motion, AnimatePresence } from "framer-motion";
import { Brain, CheckCircle2, XCircle, CircleSlash, AlertTriangle, ArrowRight, Zap } from "lucide-react";
import { strategyMeta, timeAgo, fmtUsd } from "../lib/format";

const outcomeMeta = {
  approved: { color: "#00F0B5", bg: "rgba(0,240,181,0.1)", Icon: CheckCircle2, label: "APPROVED" },
  rejected: { color: "#FF3B69", bg: "rgba(255,59,105,0.1)", Icon: XCircle, label: "REJECTED" },
  skipped: { color: "#64748b", bg: "rgba(100,116,139,0.12)", Icon: CircleSlash, label: "NO-OP" },
  error: { color: "#FFB800", bg: "rgba(255,184,0,0.1)", Icon: AlertTriangle, label: "ERROR" },
};

const VerdictBlock = ({ v }) => {
  if (!v) return null;
  const conf = Math.round((v.confidence || 0) * 100);
  return (
    <div className="term-well p-2.5 mt-2">
      <div className="flex items-center gap-2 mb-1.5">
        <Brain size={12} className="text-[#9D4EDD]" />
        <span className="text-[10px] font-mono uppercase tracking-wider text-slate-500">
          {v.source?.startsWith("featherless") ? "Featherless AI Signal" : v.source === "claude-sonnet-4-6" ? "Claude 4.6 Signal" : "LLM Signal"} {v.source === "fallback" && <span className="text-[#FFB800]">· FALLBACK</span>}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5 mb-1.5 text-[10px] font-mono">
        <span className="px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-300">regime: {v.regime}</span>
        <span className="px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-300">dir: {v.direction}</span>
        <span className="px-1.5 py-0.5 rounded" style={{ background: "rgba(157,78,221,0.12)", color: "#c99bf5" }}>conf: {conf}%</span>
      </div>
      <div className="h-1 rounded-full bg-white/[0.06] overflow-hidden mb-1.5">
        <div className="h-full rounded-full" style={{ width: `${conf}%`, background: conf >= 50 ? "#00F0B5" : "#FFB800" }} />
      </div>
      <p className="text-[11px] leading-relaxed text-slate-400 italic">"{v.rationale}"</p>
    </div>
  );
};

const GateChecklist = ({ checks }) => (
  <div className="grid grid-cols-1 gap-1 mt-2">
    {checks.map((c, i) => (
      <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
        {c.passed ? <CheckCircle2 size={11} className="text-[#00F0B5] shrink-0" /> : <XCircle size={11} className="text-[#FF3B69] shrink-0" />}
        <span className="text-slate-400 flex-1">{c.label}</span>
        <span className="text-slate-600">{c.detail}</span>
      </div>
    ))}
  </div>
);

export const AgentReasoningPanel = ({ decisions, showGate = true, height = "540px", title = "Agent Decision Engine", onTradeOpportunity }) => {
  return (
    <div className="term-card flex flex-col overflow-hidden" style={{ height }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-2 w-2 rounded-full bg-[#00F0B5] opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-[#00F0B5]" />
          </span>
          <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">{title}</h3>
        </div>
        <span className="text-[10px] font-mono text-slate-600">LLM → RULES → RISK GATE</span>
      </div>
      <div data-testid="agent-reasoning-feed" className="flex-1 overflow-y-auto p-3 space-y-2.5">
        <AnimatePresence initial={false}>
          {(decisions || []).map((d) => {
            const om = outcomeMeta[d.outcome] || outcomeMeta.skipped;
            const meta = strategyMeta[d.strategy] || {};
            return (
              <motion.div
                key={d.id} data-testid={`llm-verdict-card-${d.id}`}
                initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
                className="term-well p-3 border-l-2" style={{ borderLeftColor: om.color }}>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-mono font-bold text-slate-100 text-sm">{d.underlying}</span>
                    {d.strategy && (
                      <span className="px-1.5 py-0.5 rounded text-[9px] font-bold font-mono" style={{ color: meta.color, background: meta.bg }}>
                        {meta.label}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <span data-testid={`risk-gate-status-${d.id}`} className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold font-mono"
                      style={{ color: om.color, background: om.bg }}>
                      <om.Icon size={10} /> {om.label}
                    </span>
                  </div>
                </div>

                {d.verdict && <VerdictBlock v={d.verdict} />}

                {d.proposed && (
                  <div className="flex items-center justify-between gap-2 mt-2 pt-1">
                    <div className="flex items-center gap-2 text-[10px] font-mono text-slate-500 flex-wrap">
                      <ArrowRight size={11} className="text-slate-600" />
                      <span>x{d.proposed.contracts}</span>
                      <span>Δ{d.proposed.short_delta}</span>
                      <span>{d.proposed.dte}DTE</span>
                      <span>credit {(d.proposed.credit_width_ratio * 100).toFixed(0)}%</span>
                      <span className="text-[#FFB800]">risk {fmtUsd(d.proposed.max_risk, 0)}</span>
                    </div>
                    {onTradeOpportunity && (
                      <button
                        onClick={() => onTradeOpportunity(d)}
                        className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono font-bold text-[#06090e] bg-[#00F0B5] hover:bg-[#00D4FF] transition-all shadow-[0_0_8px_rgba(0,240,181,0.2)] shrink-0"
                      >
                        <Zap size={10} /> Open Position
                      </button>
                    )}
                  </div>
                )}

                {showGate && d.gate_checks?.length > 0 && <GateChecklist checks={d.gate_checks} />}

                <div className="flex items-center justify-between mt-2 pt-2 border-t border-[var(--border)]">
                  <p className="text-[10px] font-mono leading-snug pr-2" style={{ color: om.color }}>{d.reason}</p>
                  <span className="text-[9px] font-mono text-slate-600 shrink-0">{timeAgo(d.created_at)}</span>
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        {(!decisions || decisions.length === 0) && (
          <div className="text-center text-slate-600 font-mono text-xs py-10">Awaiting first agent cycle…</div>
        )}
      </div>
    </div>
  );
};

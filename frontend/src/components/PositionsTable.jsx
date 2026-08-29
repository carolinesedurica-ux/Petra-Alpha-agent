import { motion, AnimatePresence } from "framer-motion";
import { LineChart as LineIcon, X } from "lucide-react";
import { fmtUsd, fmtNum, strategyMeta, legLabel } from "../lib/format";

export const PositionsTable = ({ positions, onClose, onPayoff, closingId }) => {
  return (
    <div className="term-card flex flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
        <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Open Positions Desk</h3>
        <span className="text-[11px] font-mono text-slate-600">{positions?.length || 0} multi-leg spreads</span>
      </div>
      <div className="overflow-x-auto">
        <table data-testid="positions-table" className="w-full min-w-[820px]">
          <thead>
            <tr className="text-[10px] font-mono uppercase tracking-wider text-slate-500 border-b border-[var(--border)]">
              {["Underlying", "Strategy", "Legs", "DTE", "Credit", "Mark", "Unreal P&L", "Risk", "Gate", ""].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <AnimatePresence>
              {(positions || []).map((p) => {
                const meta = strategyMeta[p.strategy] || {};
                const up = p.unrealized_pnl >= 0;
                return (
                  <motion.tr
                    key={p.id} data-testid={`position-row-${p.underlying}`}
                    initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                    className="border-b border-[var(--border)] hover:bg-white/[0.02] text-xs font-mono">
                    <td className="px-3 py-3 font-bold text-slate-100">{p.underlying}</td>
                    <td className="px-3 py-3">
                      <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wide"
                        style={{ color: meta.color, background: meta.bg }}>{meta.label}</span>
                    </td>
                    <td className="px-3 py-3 text-slate-300">
                      {p.legs?.map((l, i) => (
                        <span key={i} className="mr-1" style={{ color: l.side === "sell" ? "#e2e8f0" : "#64748b" }}>
                          {legLabel(l)}
                        </span>
                      ))}
                    </td>
                    <td className="px-3 py-3 text-slate-400 tabular">{fmtNum(p.dte, 1)}d</td>
                    <td className="px-3 py-3 text-slate-300 tabular">${fmtNum(p.credit, 2)}</td>
                    <td className="px-3 py-3 text-slate-400 tabular">${fmtNum(p.current_value, 2)}</td>
                    <td className="px-3 py-3 tabular font-semibold" style={{ color: up ? "#00F0B5" : "#FF3B69" }}>
                      {up ? "+" : ""}{fmtUsd(p.unrealized_pnl, 0)}
                      <span className="text-[10px] ml-1 opacity-70">({p.unrealized_pct}%)</span>
                    </td>
                    <td className="px-3 py-3 text-[#FFB800] tabular">{fmtUsd(p.max_risk, 0)}</td>
                    <td className="px-3 py-3">
                      <span className="text-[10px] px-1.5 py-0.5 rounded" style={{
                        color: p.risk_gate_score >= 100 ? "#00F0B5" : "#FFB800",
                        background: p.risk_gate_score >= 100 ? "rgba(0,240,181,0.1)" : "rgba(255,184,0,0.1)" }}>
                        {p.risk_gate_score}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex items-center gap-1.5">
                        <button onClick={() => onPayoff(p)} title="Payoff"
                          className="p-1.5 term-well hover:border-[#00D4FF] text-slate-400 hover:text-[#00D4FF] transition-colors">
                          <LineIcon size={13} />
                        </button>
                        <button data-testid={`btn-close-position-${p.underlying}`} onClick={() => onClose(p.id)}
                          disabled={closingId === p.id} title="Close"
                          className="p-1.5 term-well hover:border-[#FF3B69] text-slate-400 hover:text-[#FF3B69] transition-colors disabled:opacity-50">
                          <X size={13} />
                        </button>
                      </div>
                    </td>
                  </motion.tr>
                );
              })}
            </AnimatePresence>
          </tbody>
        </table>
        {(!positions || positions.length === 0) && (
          <div className="px-4 py-10 text-center text-slate-600 font-mono text-xs">
            NO OPEN POSITIONS — run an agent cycle to deploy capital
          </div>
        )}
      </div>
    </div>
  );
};

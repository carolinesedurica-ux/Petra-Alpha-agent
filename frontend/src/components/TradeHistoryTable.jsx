import { strategyMeta, fmtUsd, timeAgo } from "../lib/format";

const exitMeta = {
  take_profit: { c: "#00F0B5", l: "TP 50%" },
  stop_loss: { c: "#FF3B69", l: "STOP 2×" },
  time_exit: { c: "#FFB800", l: "TIME" },
  manual: { c: "#00D4FF", l: "MANUAL" },
};

export const TradeHistoryTable = ({ trades }) => (
  <div className="overflow-x-auto">
    <table data-testid="trade-history-table" className="w-full min-w-[720px]">
      <thead>
        <tr className="text-[10px] font-mono uppercase tracking-wider text-slate-500 border-b border-[var(--border)]">
          {["Underlying", "Strategy", "Contracts", "Credit", "Exit", "Realized P&L", "Closed"].map((h) => (
            <th key={h} className="text-left px-3 py-2 font-medium whitespace-nowrap">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {(trades || []).map((t) => {
          const meta = strategyMeta[t.strategy] || {};
          const em = exitMeta[t.exit_reason] || { c: "#64748b", l: t.exit_reason };
          const up = t.realized_pnl >= 0;
          return (
            <tr key={t.id} className="border-b border-[var(--border)] hover:bg-white/[0.02] text-xs font-mono">
              <td className="px-3 py-2.5 font-bold text-slate-100">{t.underlying}</td>
              <td className="px-3 py-2.5">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold" style={{ color: meta.color, background: meta.bg }}>{meta.label}</span>
              </td>
              <td className="px-3 py-2.5 text-slate-400 tabular">x{t.contracts}</td>
              <td className="px-3 py-2.5 text-slate-400 tabular">${t.credit?.toFixed(2)}</td>
              <td className="px-3 py-2.5"><span className="text-[10px] font-bold" style={{ color: em.c }}>{em.l}</span></td>
              <td className="px-3 py-2.5 tabular font-semibold" style={{ color: up ? "#00F0B5" : "#FF3B69" }}>
                {up ? "+" : ""}{fmtUsd(t.realized_pnl, 0)}
              </td>
              <td className="px-3 py-2.5 text-slate-600">{t.closed_at ? timeAgo(t.closed_at) : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
    {(!trades || trades.length === 0) && (
      <div className="px-4 py-8 text-center text-slate-600 font-mono text-xs">No closed trades yet</div>
    )}
  </div>
);

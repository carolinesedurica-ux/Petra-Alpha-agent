import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { LineChart as LineIcon, X, CheckCircle2, AlertCircle, Clock, Layers } from "lucide-react";
import { fmtUsd, fmtNum, strategyMeta, legLabel, timeAgo } from "../lib/format";

const exitMeta = {
  take_profit: { c: "#00F0B5", bg: "rgba(0,240,181,0.12)", l: "TP 50%" },
  stop_loss: { c: "#FF3B69", bg: "rgba(255,59,105,0.12)", l: "STOP 2×" },
  time_exit: { c: "#FFB800", bg: "rgba(255,184,0,0.12)", l: "TIME" },
  manual: { c: "#00D4FF", bg: "rgba(0,212,255,0.12)", l: "MANUAL" },
};

export const PositionsTable = ({ positions = [], trades = [], onClose, onPayoff, closingId }) => {
  const [filter, setFilter] = useState("all"); // 'all' | 'open' | 'closed'

  // Combine and sort positions: open first (by opened_at desc), then closed (by closed_at desc)
  const openList = useMemo(() => {
    return (positions || []).map((p) => ({
      ...p,
      status: "open",
    }));
  }, [positions]);

  const closedList = useMemo(() => {
    return (trades || []).map((t) => ({
      ...t,
      status: "closed",
    }));
  }, [trades]);

  const displayedItems = useMemo(() => {
    if (filter === "open") return openList;
    if (filter === "closed") return closedList;
    return [...openList, ...closedList];
  }, [filter, openList, closedList]);

  // Aggregate metrics
  const totalOpenRisk = openList.reduce((acc, p) => acc + (p.max_risk || 0), 0);
  const totalUnrealized = openList.reduce((acc, p) => acc + (p.unrealized_pnl || 0), 0);
  const totalRealized = closedList.reduce((acc, t) => acc + (t.realized_pnl || 0), 0);
  const winCount = closedList.filter((t) => (t.realized_pnl || 0) > 0).length;
  const winRate = closedList.length > 0 ? Math.round((winCount / closedList.length) * 100) : null;

  return (
    <div className="term-card flex flex-col overflow-hidden">
      {/* Header with Title and Filter Buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-b border-[var(--border)] bg-[#0c111a]">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Layers size={15} className="text-[#00F0B5]" />
            <h3 className="text-sm font-mono uppercase tracking-wider text-slate-200 font-bold">
              Positions Desk
            </h3>
          </div>
          <span className="text-[11px] font-mono text-slate-500">
            {openList.length} Open · {closedList.length} Closed
          </span>
        </div>

        {/* Filter Pills */}
        <div className="flex items-center gap-1 bg-[#131b29] p-0.5 rounded border border-[var(--border)] font-mono text-xs">
          <button
            type="button"
            onClick={() => setFilter("all")}
            className={`px-2.5 py-1 rounded transition-colors ${
              filter === "all"
                ? "bg-[#00F0B5]/15 text-[#00F0B5] font-bold border border-[#00F0B5]/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            All ({openList.length + closedList.length})
          </button>
          <button
            type="button"
            onClick={() => setFilter("open")}
            className={`px-2.5 py-1 rounded transition-colors flex items-center gap-1.5 ${
              filter === "open"
                ? "bg-[#00F0B5]/15 text-[#00F0B5] font-bold border border-[#00F0B5]/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <span className="w-1.5 h-1.5 rounded-full bg-[#00F0B5]" />
            Open ({openList.length})
          </button>
          <button
            type="button"
            onClick={() => setFilter("closed")}
            className={`px-2.5 py-1 rounded transition-colors ${
              filter === "closed"
                ? "bg-[#38BDF8]/15 text-[#38BDF8] font-bold border border-[#38BDF8]/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Closed ({closedList.length})
          </button>
        </div>
      </div>

      {/* Desk Quick Metrics Strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 px-4 py-2 border-b border-[var(--border)] bg-[#080d14] text-[11px] font-mono">
        <div>
          <span className="text-slate-500">Open Risk: </span>
          <span className="text-[#FFB800] font-semibold">{fmtUsd(totalOpenRisk, 0)}</span>
        </div>
        <div>
          <span className="text-slate-500">Unrealized: </span>
          <span className={`font-semibold ${totalUnrealized >= 0 ? "text-[#00F0B5]" : "text-[#FF3B69]"}`}>
            {totalUnrealized >= 0 ? "+" : ""}{fmtUsd(totalUnrealized, 0)}
          </span>
        </div>
        <div>
          <span className="text-slate-500">Realized: </span>
          <span className={`font-semibold ${totalRealized >= 0 ? "text-[#00F0B5]" : "text-[#FF3B69]"}`}>
            {totalRealized >= 0 ? "+" : ""}{fmtUsd(totalRealized, 0)}
          </span>
        </div>
        <div>
          <span className="text-slate-500">Win Rate: </span>
          <span className="text-slate-300 font-semibold">{winRate != null ? `${winRate}%` : "—"}</span>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table data-testid="positions-table" className="w-full min-w-[860px]">
          <thead>
            <tr className="text-[10px] font-mono uppercase tracking-wider text-slate-500 border-b border-[var(--border)] bg-[#0a0f18]">
              {["Status", "Underlying", "Strategy", "Legs", "DTE / Closed", "Credit", "Mark / Exit", "P&L", "Max Risk", "Gate", "Actions"].map((h) => (
                <th key={h} className="text-left px-3 py-2 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <AnimatePresence>
              {displayedItems.map((p) => {
                const isOpen = p.status === "open";
                const meta = strategyMeta[p.strategy] || { label: p.strategy, color: "#94a3b8", bg: "rgba(148,163,184,0.1)" };
                const pnl = isOpen ? p.unrealized_pnl : p.realized_pnl;
                const up = (pnl || 0) >= 0;
                const em = !isOpen ? (exitMeta[p.exit_reason] || { c: "#94a3b8", bg: "rgba(255,255,255,0.05)", l: p.exit_reason || "CLOSED" }) : null;

                return (
                  <motion.tr
                    key={p.id}
                    data-testid={`position-row-${p.underlying}`}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className={`border-b border-[var(--border)] hover:bg-white/[0.02] text-xs font-mono ${
                      !isOpen ? "opacity-80" : ""
                    }`}
                  >
                    {/* Status Pill */}
                    <td className="px-3 py-3 whitespace-nowrap">
                      {isOpen ? (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold text-[#00F0B5] bg-[#00F0B5]/10 border border-[#00F0B5]/25">
                          <span className="w-1.5 h-1.5 rounded-full bg-[#00F0B5] animate-pulse" />
                          OPEN
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-bold text-slate-400 bg-white/[0.05] border border-white/[0.08]">
                          CLOSED
                        </span>
                      )}
                    </td>

                    {/* Underlying */}
                    <td className="px-3 py-3 font-bold text-slate-100 whitespace-nowrap">
                      {p.underlying}
                    </td>

                    {/* Strategy */}
                    <td className="px-3 py-3 whitespace-nowrap">
                      <span
                        className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wide"
                        style={{ color: meta.color, background: meta.bg }}
                      >
                        {meta.label}
                      </span>
                    </td>

                    {/* Legs */}
                    <td className="px-3 py-3 text-slate-300 whitespace-nowrap">
                      {p.legs && p.legs.length > 0 ? (
                        p.legs.map((l, i) => (
                          <span key={i} className="mr-1" style={{ color: l.side === "sell" ? "#e2e8f0" : "#64748b" }}>
                            {legLabel(l)}
                          </span>
                        ))
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>

                    {/* DTE / Closed Time */}
                    <td className="px-3 py-3 text-slate-400 tabular whitespace-nowrap">
                      {isOpen ? (
                        <span>{fmtNum(p.dte, 1)}d</span>
                      ) : (
                        <span className="text-slate-500">{p.closed_at ? timeAgo(p.closed_at) : "Closed"}</span>
                      )}
                    </td>

                    {/* Credit */}
                    <td className="px-3 py-3 text-slate-300 tabular whitespace-nowrap">
                      ${fmtNum(p.credit, 2)}
                    </td>

                    {/* Current Mark / Exit Value */}
                    <td className="px-3 py-3 text-slate-400 tabular whitespace-nowrap">
                      ${fmtNum(isOpen ? p.current_value : (p.exit_value ?? p.current_value), 2)}
                    </td>

                    {/* P&L */}
                    <td className="px-3 py-3 tabular font-semibold whitespace-nowrap" style={{ color: up ? "#00F0B5" : "#FF3B69" }}>
                      {up ? "+" : ""}{fmtUsd(pnl, 0)}
                      {isOpen && p.unrealized_pct != null && (
                        <span className="text-[10px] ml-1 opacity-70">({p.unrealized_pct}%)</span>
                      )}
                      {!isOpen && em && (
                        <span
                          className="ml-1.5 px-1.5 py-0.2 rounded text-[9px] font-bold uppercase tracking-wider"
                          style={{ color: em.c, background: em.bg }}
                        >
                          {em.l}
                        </span>
                      )}
                    </td>

                    {/* Max Risk */}
                    <td className="px-3 py-3 text-[#FFB800] tabular whitespace-nowrap">
                      {fmtUsd(p.max_risk, 0)}
                    </td>

                    {/* Risk Gate Score */}
                    <td className="px-3 py-3 whitespace-nowrap">
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded"
                        style={{
                          color: (p.risk_gate_score || 100) >= 100 ? "#00F0B5" : "#FFB800",
                          background: (p.risk_gate_score || 100) >= 100 ? "rgba(0,240,181,0.1)" : "rgba(255,184,0,0.1)",
                        }}
                      >
                        {p.risk_gate_score ?? 100}
                      </span>
                    </td>

                    {/* Actions */}
                    <td className="px-3 py-3 whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        {onPayoff && (
                          <button
                            type="button"
                            onClick={() => onPayoff(p)}
                            title="View Payoff Diagram"
                            className="p-1.5 term-well hover:border-[#00D4FF] text-slate-400 hover:text-[#00D4FF] transition-colors"
                          >
                            <LineIcon size={13} />
                          </button>
                        )}
                        {isOpen ? (
                          <button
                            type="button"
                            data-testid={`btn-close-position-${p.underlying}`}
                            onClick={() => onClose(p.id)}
                            disabled={closingId === p.id}
                            title="Close Position Now"
                            className="p-1.5 term-well hover:border-[#FF3B69] text-slate-400 hover:text-[#FF3B69] transition-colors disabled:opacity-50"
                          >
                            <X size={13} />
                          </button>
                        ) : (
                          <span className="p-1.5 text-slate-600 cursor-default" title="Trade Closed">
                            <CheckCircle2 size={13} />
                          </span>
                        )}
                      </div>
                    </td>
                  </motion.tr>
                );
              })}
            </AnimatePresence>
          </tbody>
        </table>

        {displayedItems.length === 0 && (
          <div className="px-4 py-12 text-center text-slate-500 font-mono text-xs space-y-1">
            <div className="font-bold text-slate-400 uppercase tracking-wider">
              {filter === "all" && "No Positions Recorded"}
              {filter === "open" && "No Active Open Positions"}
              {filter === "closed" && "No Closed Positions Yet"}
            </div>
            <p className="text-[11px] text-slate-600">
              {filter === "all" && "Run an agent cycle or submit an order via the Trading Desk to deploy options capital."}
              {filter === "open" && (closedList.length > 0 ? `${closedList.length} realized trades available in the Closed tab.` : "Capital is idle. Launch an AI cycle or transmit an order.")}
              {filter === "closed" && "Active positions will move here automatically upon take-profit, stop-loss, or manual exit."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

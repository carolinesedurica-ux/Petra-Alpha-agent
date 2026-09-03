import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Brain, CheckCircle2, XCircle, ShieldCheck, Zap, AlertTriangle, ArrowRight, RefreshCw, Loader2 } from "lucide-react";
import { evaluateOpportunity, openPosition } from "../lib/api";
import { strategyMeta, fmtUsd } from "../lib/format";
import { toast } from "sonner";

const UNIVERSE = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "META"];

export const ManualTradeModal = ({ open, onClose, initialData, onSuccess }) => {
  const [symbol, setSymbol] = useState(initialData?.underlying || initialData?.symbol || "QQQ");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [data, setData] = useState(null);
  const [contracts, setContracts] = useState(2);

  // Sync if initialData is provided
  useEffect(() => {
    if (initialData) {
      if (initialData.proposal) {
        setData({
          symbol: initialData.underlying || initialData.symbol,
          verdict: initialData.verdict,
          proposal: initialData.proposal,
          gate_checks: initialData.gate_checks || [],
          gate_passed: initialData.gate_passed ?? true,
          gate_score: initialData.gate_score ?? 100,
          market: initialData.market_snapshot || {},
        });
        setContracts(initialData.proposal.contracts || 2);
        setSymbol(initialData.underlying || initialData.symbol || "QQQ");
      } else if (initialData.underlying || initialData.symbol) {
        const sym = initialData.underlying || initialData.symbol;
        setSymbol(sym);
        handleAnalyze(sym);
      }
    } else if (open && !data) {
      handleAnalyze(symbol);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialData, open]);

  const handleAnalyze = async (symToAnalyze) => {
    const s = symToAnalyze || symbol;
    setLoading(true);
    try {
      const res = await evaluateOpportunity(s);
      setData(res);
      if (res.proposal?.contracts) {
        setContracts(res.proposal.contracts);
      }
    } catch (err) {
      toast.error(`Analysis failed for ${s}: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleExecute = async () => {
    if (!data?.proposal) {
      toast.error("No valid proposal to execute.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await openPosition({
        proposal: data.proposal,
        contracts: contracts,
        paper_sim: true,
      });
      toast.success(res.message || `Successfully opened ${data.proposal.strategy} on ${data.symbol}`);
      if (onSuccess) onSuccess(res);
      onClose();
    } catch (err) {
      toast.error(`Order execution failed: ${err.response?.data?.detail || err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  const proposal = data?.proposal;
  const verdict = data?.verdict;
  const strat = strategyMeta[proposal?.strategy] || { label: proposal?.strategy || "SPREAD", color: "#00F0B5", bg: "rgba(0,240,181,0.1)" };
  const conf = verdict ? Math.round((verdict.confidence || 0) * 100) : 0;

  // Real-time calculations with current contracts
  const activeContracts = Math.max(1, contracts || 1);
  const totalCredit = proposal ? Math.round(proposal.credit * 100 * activeContracts) : 0;
  const maxRisk = proposal ? Math.round((proposal.width - proposal.credit) * 100 * activeContracts) : 0;
  const tpTarget = proposal ? (proposal.credit * 0.50).toFixed(2) : "0.00";
  const stopTarget = proposal ? (proposal.credit * 2.00).toFixed(2) : "0.00";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 10 }}
        className="term-card w-full max-w-3xl max-h-[92vh] flex flex-col overflow-hidden border border-[var(--border-accent)] shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--border)] bg-[#0c111a]">
          <div className="flex items-center gap-2.5">
            <div className="h-7 w-7 rounded flex items-center justify-center border border-[var(--border-accent)] bg-[#00F0B5]/10">
              <Zap size={15} className="text-[#00F0B5]" />
            </div>
            <div>
              <h3 className="font-mono text-xs sm:text-sm font-bold tracking-wider text-slate-100 uppercase">
                Trade Opportunity // Manual Execution
              </h3>
              <p className="text-[10px] font-mono text-slate-500">
                Agent reasoning · Greeks & strikes · Hard risk gate telemetry
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded hover:bg-white/[0.05] transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Universe Quick Ticker Bar */}
        <div className="flex items-center gap-1.5 px-5 py-2.5 border-b border-[var(--border)] bg-[#080d14] overflow-x-auto">
          <span className="text-[10px] font-mono text-slate-500 shrink-0 mr-1 uppercase">Ticker:</span>
          {UNIVERSE.map((sym) => (
            <button
              key={sym}
              onClick={() => {
                setSymbol(sym);
                handleAnalyze(sym);
              }}
              disabled={loading || submitting}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                symbol === sym
                  ? "bg-[#00F0B5] text-[#06090e] shadow-[0_0_12px_rgba(0,240,181,0.3)]"
                  : "bg-white/[0.04] text-slate-400 hover:bg-white/[0.08] hover:text-white"
              }`}
            >
              {sym}
            </button>
          ))}
          <button
            onClick={() => handleAnalyze(symbol)}
            disabled={loading || submitting}
            className="ml-auto flex items-center gap-1 px-3 py-1 rounded text-[11px] font-mono bg-white/[0.06] text-slate-300 hover:text-white hover:bg-white/[0.1] shrink-0"
          >
            <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
            Re-Analyze
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {loading && (
            <div className="py-16 flex flex-col items-center justify-center space-y-3">
              <Loader2 size={32} className="animate-spin text-[#00F0B5]" />
              <p className="text-xs font-mono text-slate-400">
                Evaluating {symbol}: Fetching live options chain & querying LLM signal...
              </p>
            </div>
          )}

          {!loading && data && (
            <>
              {/* Agent Verdict Card */}
              {verdict && (
                <div className="term-well p-4 border-l-2 border-[#9D4EDD] bg-[#0c111a]">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <Brain size={15} className="text-[#9D4EDD]" />
                      <span className="text-xs font-mono font-bold text-slate-200">
                        {symbol} // AI REGIME & SIGNAL
                      </span>
                      <span
                        className="px-2 py-0.5 rounded text-[10px] font-bold font-mono"
                        style={{ color: strat.color, background: strat.bg }}
                      >
                        {strat.label}
                      </span>
                    </div>
                    <span className="text-[11px] font-mono" style={{ color: conf >= 50 ? "#00F0B5" : "#FFB800" }}>
                      Confidence: {conf}%
                    </span>
                  </div>

                  <div className="flex flex-wrap gap-2 text-xs font-mono text-slate-300 mb-2">
                    <span className="px-2 py-0.5 rounded bg-white/[0.04]">Regime: {verdict.regime}</span>
                    <span className="px-2 py-0.5 rounded bg-white/[0.04]">Direction: {verdict.direction}</span>
                    {data.market?.price && (
                      <span className="px-2 py-0.5 rounded bg-white/[0.04]">Spot: ${data.market.price}</span>
                    )}
                    {data.market?.iv && (
                      <span className="px-2 py-0.5 rounded bg-white/[0.04]">IV: {(data.market.iv * 100).toFixed(1)}%</span>
                    )}
                  </div>

                  <p className="text-xs font-sans text-slate-400 italic leading-relaxed">
                    "{verdict.rationale}"
                  </p>
                </div>
              )}

              {/* The 7 Hard Risk Gates Telemetry */}
              <div className="term-well p-3.5 bg-[#0c111a]">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-1.5">
                    <ShieldCheck size={14} className="text-[#00F0B5]" />
                    <span className="text-xs font-mono font-bold text-slate-300 uppercase">
                      Risk Gate Audit (Score: {data.gate_score}/100)
                    </span>
                  </div>
                  <span
                    className="px-2 py-0.5 rounded text-[10px] font-mono font-bold"
                    style={{
                      color: data.gate_passed ? "#00F0B5" : "#FF3B69",
                      background: data.gate_passed ? "rgba(0,240,181,0.1)" : "rgba(255,59,105,0.1)",
                    }}
                  >
                    {data.gate_passed ? "ALL 7 GATES PASSED" : "RISK GATE WARNING"}
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 text-xs font-mono">
                  {(data.gate_checks || []).map((c, i) => (
                    <div key={i} className="flex items-center gap-2 p-1 rounded bg-white/[0.02]">
                      {c.passed ? (
                        <CheckCircle2 size={12} className="text-[#00F0B5] shrink-0" />
                      ) : (
                        <XCircle size={12} className="text-[#FF3B69] shrink-0" />
                      )}
                      <span className="text-slate-400 text-[11px] truncate flex-1">{c.label}</span>
                      <span className="text-slate-500 text-[10px] shrink-0">{c.detail}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Spread Details & Legs */}
              {proposal && (
                <div className="term-well p-3.5 bg-[#0c111a]">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-mono font-bold text-slate-300 uppercase">
                      Defined-Risk Spread Legs ({proposal.dte} DTE · Expiry {proposal.expiry_ts?.split("T")[0]})
                    </span>
                    <span className="text-xs font-mono text-[#00F0B5]">
                      Net Credit: ${proposal.credit.toFixed(2)}/sh
                    </span>
                  </div>

                  <div className="border border-[var(--border)] rounded overflow-hidden">
                    <table className="w-full text-xs font-mono">
                      <thead className="bg-white/[0.04] text-slate-400">
                        <tr>
                          <th className="py-1.5 px-3 text-left">Action</th>
                          <th className="py-1.5 px-3 text-left">Strike</th>
                          <th className="py-1.5 px-3 text-left">Delta</th>
                          <th className="py-1.5 px-3 text-right">Option Symbol</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[var(--border)]">
                        {(proposal.legs || []).map((leg, idx) => (
                          <tr key={idx} className="hover:bg-white/[0.02]">
                            <td className="py-2 px-3">
                              <span
                                className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                                  leg.side === "sell"
                                    ? "bg-[#00F0B5]/15 text-[#00F0B5]"
                                    : "bg-blue-500/15 text-blue-400"
                                }`}
                              >
                                {leg.side.toUpperCase()} TO OPEN
                              </span>
                            </td>
                            <td className="py-2 px-3 text-slate-200 font-semibold">
                              ${leg.strike} {leg.option_type?.toUpperCase()}
                            </td>
                            <td className="py-2 px-3 text-slate-400">
                              Δ {leg.delta ? leg.delta.toFixed(3) : "—"}
                            </td>
                            <td className="py-2 px-3 text-right text-slate-500 text-[11px] font-mono">
                              {leg.symbol}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Sizing & Payoff Metrics */}
              {proposal && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                  <div className="term-well p-3">
                    <span className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Contracts</span>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setContracts((c) => Math.max(1, c - 1))}
                        disabled={contracts <= 1}
                        className="h-6 w-6 rounded bg-white/[0.06] text-slate-200 hover:bg-white/[0.12] flex items-center justify-center font-mono font-bold"
                      >
                        -
                      </button>
                      <span className="text-sm font-mono font-bold text-white px-1">{activeContracts}</span>
                      <button
                        onClick={() => setContracts((c) => Math.min(10, c + 1))}
                        disabled={contracts >= 10}
                        className="h-6 w-6 rounded bg-white/[0.06] text-slate-200 hover:bg-white/[0.12] flex items-center justify-center font-mono font-bold"
                      >
                        +
                      </button>
                    </div>
                  </div>

                  <div className="term-well p-3">
                    <span className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Total Credit</span>
                    <span className="text-sm font-mono font-bold text-[#00F0B5]">{fmtUsd(totalCredit, 0)}</span>
                    <span className="text-[9px] font-mono text-slate-500 block">Immediate inflow</span>
                  </div>

                  <div className="term-well p-3">
                    <span className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Max Risk</span>
                    <span className="text-sm font-mono font-bold text-[#FFB800]">{fmtUsd(maxRisk, 0)}</span>
                    <span className="text-[9px] font-mono text-slate-500 block">Strict stop-out</span>
                  </div>

                  <div className="term-well p-3">
                    <span className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Targets</span>
                    <div className="text-[10px] font-mono text-slate-300">
                      <div>TP @ ${tpTarget}</div>
                      <div>SL @ ${stopTarget}</div>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {!loading && !data && (
            <div className="py-12 text-center text-slate-500 font-mono text-xs">
              Select an underlying ticker above to evaluate trade opportunities.
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="flex items-center justify-between px-5 py-3.5 border-t border-[var(--border)] bg-[#080d14]">
          <span className="text-[11px] font-mono text-slate-500">
            {data?.gate_passed ? "Ready for Alpaca multi-leg execution" : "Operator discretion required"}
          </span>

          <div className="flex items-center gap-2.5">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded text-xs font-mono text-slate-400 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleExecute}
              disabled={loading || submitting || !data?.proposal}
              className="flex items-center gap-2 px-5 py-2 rounded font-mono text-xs font-bold text-[#06090e] bg-gradient-to-r from-[#00F0B5] to-[#00D4FF] hover:opacity-90 disabled:opacity-50 shadow-[0_0_20px_rgba(0,240,181,0.25)] transition-all"
            >
              {submitting ? (
                <>
                  <Loader2 size={13} className="animate-spin" />
                  SUBMITTING ORDER…
                </>
              ) : (
                <>
                  <Zap size={14} />
                  OPEN POSITION ({activeContracts}x)
                </>
              )}
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  );
};

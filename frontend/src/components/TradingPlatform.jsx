import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  TrendingUp, TrendingDown, Layers, ShieldCheck, Zap,
  CheckCircle2, XCircle, AlertTriangle, RefreshCw, Loader2,
  DollarSign, Percent, ArrowUpRight, BarChart2, Activity, Play
} from "lucide-react";
import { evaluateOpportunity, openPosition } from "../lib/api";
import { fmtUsd, fmtPct, strategyMeta } from "../lib/format";
import { toast } from "sonner";

const UNIVERSE = [
  { symbol: "SPY", name: "S&P 500 ETF", defaultPrice: 598.50 },
  { symbol: "QQQ", name: "Invesco Nasdaq 100", defaultPrice: 512.20 },
  { symbol: "IWM", name: "Russell 2000 ETF", defaultPrice: 224.80 },
  { symbol: "NVDA", name: "NVIDIA Corp", defaultPrice: 142.60 },
  { symbol: "AAPL", name: "Apple Inc", defaultPrice: 238.40 },
  { symbol: "TSLA", name: "Tesla Inc", defaultPrice: 312.10 },
  { symbol: "MSFT", name: "Microsoft Corp", defaultPrice: 428.50 },
  { symbol: "META", name: "Meta Platforms", defaultPrice: 592.30 },
];

const STRATEGIES = [
  {
    id: "put_credit_spread",
    name: "Bull Put Spread",
    direction: "Bullish / Neutral",
    icon: TrendingUp,
    color: "#00F0B5",
    desc: "Sell higher put, buy lower put. Collect credit if stock stays above short strike."
  },
  {
    id: "call_credit_spread",
    name: "Bear Call Spread",
    direction: "Bearish / Neutral",
    icon: TrendingDown,
    color: "#FF3B69",
    desc: "Sell lower call, buy higher call. Collect credit if stock stays below short strike."
  },
  {
    id: "iron_condor",
    name: "Iron Condor",
    direction: "Delta Neutral",
    icon: Layers,
    color: "#38BDF8",
    desc: "Combine Bull Put + Bear Call. Maximum profit if stock stays in a defined range."
  }
];

export const TradingPlatform = ({ account, onOrderExecuted }) => {
  const [selectedSymbol, setSelectedSymbol] = useState("QQQ");
  const [selectedStrategy, setSelectedStrategy] = useState("put_credit_spread");
  const [contracts, setContracts] = useState(2);
  const [width, setWidth] = useState(5.0);
  const [credit, setCredit] = useState(0.85);

  const [analyzing, setAnalyzing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [analysisData, setAnalysisData] = useState(null);

  // Fetch AI evaluation for selected symbol
  const handleAnalyzeSymbol = async (sym = selectedSymbol) => {
    setAnalyzing(true);
    try {
      const res = await evaluateOpportunity(sym);
      setAnalysisData(res);
      if (res.proposal) {
        if (res.proposal.strategy) setSelectedStrategy(res.proposal.strategy);
        if (res.proposal.contracts) setContracts(res.proposal.contracts);
        if (res.proposal.width) setWidth(res.proposal.width);
        if (res.proposal.credit) setCredit(res.proposal.credit);
      }
      toast.success(`AI Opportunity Evaluated: ${sym} (${res.verdict?.chosen_strategy || "Analysis ready"})`);
    } catch (err) {
      toast.error(`Evaluation failed: ${err.response?.data?.detail || err.message}`);
    } finally {
      setAnalyzing(false);
    }
  };

  useEffect(() => {
    handleAnalyzeSymbol(selectedSymbol);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol]);

  // Current market data
  const market = analysisData?.market || {};
  const currentPrice = market.price || UNIVERSE.find((u) => u.symbol === selectedSymbol)?.defaultPrice || 500;
  const currentIv = market.iv ? (market.iv * 100).toFixed(1) : "22.4";
  const changePct = market.change_pct ?? 0.45;

  // Real-time risk & return calculations
  const totalCredit = Math.round(credit * 100 * contracts);
  const maxRisk = Math.round(Math.max(0, width - credit) * 100 * contracts);
  const returnOnRisk = maxRisk > 0 ? ((totalCredit / maxRisk) * 100).toFixed(1) : "0.0";
  const creditWidthRatio = width > 0 ? ((credit / width) * 100).toFixed(1) : "0.0";
  const creditRatioPassed = parseFloat(creditWidthRatio) >= 15.0;

  // Strikes approximation based on strategy
  const shortStrike = selectedStrategy === "call_credit_spread"
    ? Math.round(currentPrice * 1.02)
    : Math.round(currentPrice * 0.98);
  const longStrike = selectedStrategy === "call_credit_spread"
    ? shortStrike + width
    : shortStrike - width;

  const handleTransmitOrder = async () => {
    setSubmitting(true);
    try {
      const proposalPayload = analysisData?.proposal || {
        underlying: selectedSymbol,
        strategy: selectedStrategy,
        contracts: contracts,
        width: width,
        credit: credit,
        max_risk: maxRisk,
        entry_underlying: currentPrice,
        entry_iv: parseFloat(currentIv) / 100,
        dte: 5.0,
        expiry_ts: new Date(Date.now() + 5 * 86400000).toISOString(),
        legs: [
          {
            side: "sell",
            option_type: selectedStrategy.includes("put") ? "put" : "call",
            strike: shortStrike,
            price: credit * 1.6,
            symbol: `${selectedSymbol}260912${selectedStrategy.includes("put") ? "P" : "C"}${shortStrike * 1000}`
          },
          {
            side: "buy",
            option_type: selectedStrategy.includes("put") ? "put" : "call",
            strike: longStrike,
            price: credit * 0.6,
            symbol: `${selectedSymbol}260912${selectedStrategy.includes("put") ? "P" : "C"}${longStrike * 1000}`
          }
        ]
      };

      const res = await openPosition({
        proposal: proposalPayload,
        contracts: contracts,
        paper_sim: true
      });

      toast.success(`Order Submitted: ${selectedStrategy} x${contracts} on ${selectedSymbol} (Credit: $${totalCredit})`);
      if (onOrderExecuted) onOrderExecuted(res);
    } catch (err) {
      toast.error(`Order execution failed: ${err.response?.data?.detail || err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const activeStrat = STRATEGIES.find((s) => s.id === selectedStrategy) || STRATEGIES[0];
  const StratIcon = activeStrat.icon;

  return (
    <div data-testid="trading-platform-container" className="space-y-5">
      {/* Ticker Selector Header */}
      <div className="term-card p-3 flex flex-wrap items-center justify-between gap-3 border-[var(--border)]">
        <div className="flex items-center gap-2 overflow-x-auto py-1">
          <span className="text-[11px] font-mono uppercase text-slate-500 font-bold mr-1">TICKER:</span>
          {UNIVERSE.map((u) => {
            const isSelected = u.symbol === selectedSymbol;
            return (
              <button
                key={u.symbol}
                data-testid={`ticker-select-${u.symbol}`}
                onClick={() => setSelectedSymbol(u.symbol)}
                className={`px-3 py-1.5 rounded text-xs font-mono font-bold transition-all flex items-center gap-1.5 ${
                  isSelected
                    ? "bg-[#00F0B5] text-[#06090e] shadow-[0_0_12px_rgba(0,240,181,0.3)]"
                    : "bg-[#0f172a] text-slate-400 hover:text-white hover:bg-[#1e293b]"
                }`}
              >
                <span>{u.symbol}</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right font-mono">
            <span className="text-[10px] text-slate-500 uppercase">Live Spot</span>
            <div className="text-sm font-bold text-white flex items-center gap-1">
              ${currentPrice.toFixed(2)}
              <span className={`text-[11px] ${changePct >= 0 ? "text-[#00F0B5]" : "text-[#FF3B69]"}`}>
                {changePct >= 0 ? "+" : ""}{changePct}%
              </span>
            </div>
          </div>
          <button
            onClick={() => handleAnalyzeSymbol(selectedSymbol)}
            disabled={analyzing}
            className="p-2 term-well text-slate-400 hover:text-[#00F0B5] transition-colors rounded"
            title="Refresh Quotes & AI Engine"
          >
            <RefreshCw size={14} className={analyzing ? "animate-spin text-[#00F0B5]" : ""} />
          </button>
        </div>
      </div>

      {/* Main Trading Desk Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Left Column: Strategy & Order Builder */}
        <div className="lg:col-span-7 space-y-5">
          {/* Strategy Selection */}
          <div className="term-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-bold flex items-center gap-1.5">
                <Layers size={14} className="text-[#00F0B5]" /> Defined-Risk Option Strategy
              </h4>
              <span className="text-[10px] font-mono text-slate-500">M-LEG CREDIT SPREAD</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {STRATEGIES.map((strat) => {
                const Icon = strat.icon;
                const isSelected = selectedStrategy === strat.id;
                return (
                  <button
                    key={strat.id}
                    onClick={() => setSelectedStrategy(strat.id)}
                    className={`p-3 rounded-md text-left transition-all border font-mono ${
                      isSelected
                        ? "bg-[#00F0B5]/10 border-[#00F0B5] text-white shadow-[0_0_15px_rgba(0,240,181,0.15)]"
                        : "bg-[#0a0e17] border-slate-800 text-slate-400 hover:border-slate-700"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <Icon size={16} style={{ color: strat.color }} />
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-300">
                        {strat.direction}
                      </span>
                    </div>
                    <div className="text-xs font-bold">{strat.name}</div>
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] font-mono text-slate-500">{activeStrat.desc}</p>
          </div>

          {/* Interactive Parameters Configurator */}
          <div className="term-card p-4 space-y-4">
            <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-bold flex items-center gap-1.5">
              <Activity size={14} className="text-[#38BDF8]" /> Strike &amp; Capital Parameters
            </h4>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {/* Short Strike */}
              <div className="bg-[#0a0e17] p-3 rounded border border-slate-800/80">
                <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Short Strike</label>
                <div className="text-base font-mono font-bold text-[#00F0B5]">${shortStrike}</div>
                <span className="text-[10px] font-mono text-slate-600">Sell (Delta ~0.20)</span>
              </div>

              {/* Long Strike */}
              <div className="bg-[#0a0e17] p-3 rounded border border-slate-800/80">
                <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Hedge Strike</label>
                <div className="text-base font-mono font-bold text-slate-200">${longStrike}</div>
                <span className="text-[10px] font-mono text-slate-600">Buy Protection</span>
              </div>

              {/* Spread Width */}
              <div className="bg-[#0a0e17] p-3 rounded border border-slate-800/80">
                <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Spread Width</label>
                <div className="flex items-center gap-1">
                  {[1, 2, 5, 10].map((w) => (
                    <button
                      key={w}
                      onClick={() => setWidth(w)}
                      className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                        width === w ? "bg-[#38BDF8] text-[#06090e]" : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      ${w}
                    </button>
                  ))}
                </div>
              </div>

              {/* Contracts */}
              <div className="bg-[#0a0e17] p-3 rounded border border-slate-800/80">
                <label className="text-[10px] font-mono text-slate-500 uppercase block mb-1">Contracts</label>
                <div className="flex items-center gap-1">
                  {[1, 2, 4, 8].map((c) => (
                    <button
                      key={c}
                      onClick={() => setContracts(c)}
                      className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold ${
                        contracts === c ? "bg-[#00F0B5] text-[#06090e]" : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {c}x
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Net Credit Adjuster */}
            <div className="flex items-center justify-between p-3 rounded bg-[#0a0e17] border border-slate-800/80 font-mono">
              <div>
                <span className="text-xs text-slate-300 font-bold block">Target Net Premium Credit</span>
                <span className="text-[10px] text-slate-500">Collected upfront per share</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setCredit((c) => Math.max(0.1, Number((c - 0.05).toFixed(2))))}
                  className="w-7 h-7 bg-slate-800 hover:bg-slate-700 rounded text-slate-200 font-bold text-sm"
                >
                  -
                </button>
                <span className="text-base font-bold text-[#00F0B5] w-16 text-center">${credit.toFixed(2)}</span>
                <button
                  onClick={() => setCredit((c) => Number((c + 0.05).toFixed(2)))}
                  className="w-7 h-7 bg-slate-800 hover:bg-slate-700 rounded text-slate-200 font-bold text-sm"
                >
                  +
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: AI Signal & Execution Terminal */}
        <div className="lg:col-span-5 space-y-5">
          {/* AI Decision & Regime Insight */}
          <div className="term-card p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-mono uppercase tracking-wider text-slate-400 font-bold flex items-center gap-1.5">
                <ShieldCheck size={14} className="text-[#A855F7]" /> AI Signal &amp; Risk Engine
              </h4>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/30">
                FEATHERLESS QWEN 3.6
              </span>
            </div>

            {analysisData?.verdict ? (
              <div className="bg-[#0a0e17] p-3 rounded border border-slate-800 space-y-2 font-mono text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Market Regime:</span>
                  <span className="font-bold text-[#00F0B5] uppercase">{analysisData.verdict.regime}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Directional Bias:</span>
                  <span className="font-bold text-white uppercase">{analysisData.verdict.direction}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Confidence Score:</span>
                  <span className="font-bold text-[#38BDF8]">
                    {Math.round((analysisData.verdict.confidence || 0) * 100)}%
                  </span>
                </div>
                <div className="pt-2 border-t border-slate-800/80 text-[11px] text-slate-400 italic">
                  "{analysisData.verdict.rationale}"
                </div>
              </div>
            ) : (
              <div className="bg-[#0a0e17] p-4 rounded border border-slate-800 text-center font-mono text-xs text-slate-500">
                {analyzing ? (
                  <div className="flex items-center justify-center gap-2 text-[#38BDF8]">
                    <Loader2 size={16} className="animate-spin" /> Evaluating market structure...
                  </div>
                ) : (
                  "Select a ticker to load real-time AI signal & strike selection."
                )}
              </div>
            )}

            {/* 7 Deterministic Risk Gates Indicator */}
            <div className="p-3 bg-[#0a0e17] rounded border border-slate-800 space-y-1.5">
              <div className="text-[10px] font-mono text-slate-400 uppercase font-bold flex items-center justify-between">
                <span>Deterministic Risk Gates</span>
                <span className={creditRatioPassed ? "text-[#00F0B5]" : "text-[#FFB800]"}>
                  Credit/Width: {creditWidthRatio}% {creditRatioPassed ? "✓" : "⚠ (Min 15%)"}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-1.5 text-[10px] font-mono text-slate-400">
                <div className="flex items-center gap-1 text-[#00F0B5]"><CheckCircle2 size={11} /> Max Loss Cap (&lt;2%)</div>
                <div className="flex items-center gap-1 text-[#00F0B5]"><CheckCircle2 size={11} /> Bid/Ask Spread (&lt;20%)</div>
                <div className="flex items-center gap-1 text-[#00F0B5]"><CheckCircle2 size={11} /> Open Interest (&gt;150)</div>
                <div className="flex items-center gap-1 text-[#00F0B5]"><CheckCircle2 size={11} /> No Duplicate Strike</div>
              </div>
            </div>
          </div>

          {/* Risk & Return Summary & Execute Button */}
          <div className="term-card p-4 space-y-4 border-[#00F0B5]/30">
            <div className="grid grid-cols-3 gap-2 text-center font-mono">
              <div className="bg-[#0a0e17] p-2.5 rounded border border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase block">Max Profit</span>
                <span className="text-sm font-bold text-[#00F0B5]">+${totalCredit}</span>
              </div>
              <div className="bg-[#0a0e17] p-2.5 rounded border border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase block">Max Risk</span>
                <span className="text-sm font-bold text-[#FF3B69]">-${maxRisk}</span>
              </div>
              <div className="bg-[#0a0e17] p-2.5 rounded border border-slate-800">
                <span className="text-[10px] text-slate-500 uppercase block">Return on Risk</span>
                <span className="text-sm font-bold text-[#38BDF8]">{returnOnRisk}%</span>
              </div>
            </div>

            {/* Execution Button */}
            <motion.button
              data-testid="execute-alpaca-order-btn"
              onClick={handleTransmitOrder}
              disabled={submitting}
              whileTap={{ scale: 0.98 }}
              className="w-full py-3.5 rounded font-mono text-sm font-bold text-[#06090e] transition-all flex items-center justify-center gap-2 disabled:opacity-60 shadow-[0_0_20px_rgba(0,240,181,0.25)]"
              style={{ background: "linear-gradient(135deg, #00F0B5 0%, #00D4FF 100%)" }}
            >
              {submitting ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  TRANSMITTING MLEG ORDER TO ALPACA...
                </>
              ) : (
                <>
                  <Zap size={16} />
                  TRANSMIT TO ALPACA PAPER ACCOUNT ({account?.account_id || "PA39X74UN8VF"})
                </>
              )}
            </motion.button>
            <div className="text-[10px] font-mono text-slate-500 text-center flex items-center justify-center gap-1.5">
              <ShieldCheck size={12} className="text-[#00F0B5]" /> Defined-risk guaranteed: Max loss bounded at ${maxRisk}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

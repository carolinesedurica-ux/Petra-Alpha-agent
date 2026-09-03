import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X, TrendingUp, TrendingDown, ArrowUpCircle, ArrowDownCircle,
  Activity, Zap, AlertCircle, ChevronDown, BarChart3
} from "lucide-react";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer
} from "recharts";
import { useQuery, useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { getMarketBars, placeManualOrder } from "@/lib/api";

const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d));
const fmtVol = (n) => {
  if (!n) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "K";
  return String(n);
};

const ORDER_TYPES = ["market", "limit"];
const TIF_OPTIONS = ["day", "gtc", "ioc"];

const SparkChart = ({ bars, color }) => {
  if (!bars?.length) return (
    <div className="flex items-center justify-center h-full text-slate-600 text-xs font-mono">
      <BarChart3 size={16} className="mr-2" /> Loading chart…
    </div>
  );
  const data = bars.map((b) => ({ t: b.t, c: b.c }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={color} stopOpacity={0.25} />
            <stop offset="95%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="t" hide />
        <YAxis domain={["auto", "auto"]} hide />
        <Tooltip
          contentStyle={{ background: "#0c111a", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 5, fontFamily: "JetBrains Mono", fontSize: 11 }}
          labelStyle={{ color: "#64748b" }}
          formatter={(v) => [`$${fmt(v)}`, "Close"]}
          labelFormatter={() => ""}
        />
        <Area type="monotone" dataKey="c" stroke={color} strokeWidth={1.5}
              fill="url(#sparkGrad)" dot={false} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
};

export const TradeWindow = ({ open, onClose, initialSymbol, liveMarket }) => {
  const symbols = (liveMarket?.symbols || []).map((s) => s.symbol);
  const UNIVERSE = symbols.length ? symbols : ["SPY","QQQ","IWM","AAPL","MSFT","NVDA","TSLA","META"];

  const [symbol, setSymbol] = useState(initialSymbol || UNIVERSE[0]);
  const [side, setSide] = useState("buy");
  const [orderType, setOrderType] = useState("market");
  const [qty, setQty] = useState("100");
  const [limitPrice, setLimitPrice] = useState("");
  const [tif, setTif] = useState("day");
  const [fill, setFill] = useState(null);
  const [symOpen, setSymOpen] = useState(false);

  // Sync symbol when parent changes
  useEffect(() => {
    if (initialSymbol) { setSymbol(initialSymbol); setFill(null); }
  }, [initialSymbol]);

  // Live quote from market/live data
  const quote = (liveMarket?.symbols || []).find((s) => s.symbol === symbol) || {};
  const chg = quote.change_pct ?? 0;
  const color = chg >= 0 ? "#00F0B5" : "#FF3B69";

  // Bars for sparkline
  const { data: barsData, isLoading: barsLoading } = useQuery({
    queryKey: ["bars", symbol],
    queryFn: () => getMarketBars(symbol, 30),
    enabled: open && !!symbol,
    refetchInterval: 30000,
  });

  const orderMut = useMutation({
    mutationFn: placeManualOrder,
    onSuccess: (res) => {
      setFill(res);
      toast.success(`Order ${res.status?.toUpperCase()} · ${symbol} ×${qty} @ $${fmt(res.filled_price)}`);
    },
    onError: (e) => toast.error(`Order failed: ${e.response?.data?.detail || e.message}`),
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!qty || parseInt(qty) <= 0) return toast.error("Qty must be > 0");
    if (orderType === "limit" && !limitPrice) return toast.error("Limit price required");
    orderMut.mutate({
      symbol, qty: parseInt(qty), side,
      order_type: orderType,
      limit_price: orderType === "limit" ? parseFloat(limitPrice) : undefined,
    });
  };

  const handleReset = () => { setFill(null); orderMut.reset(); };

  const estimatedCost = () => {
    const p = orderType === "limit" && limitPrice ? parseFloat(limitPrice) : (quote.last || 0);
    const q = parseInt(qty) || 0;
    return p * q;
  };

  if (!open) return null;

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="tw-backdrop"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
          />

          {/* Panel — slides in from right */}
          <motion.div
            key="tw-panel"
            initial={{ x: "100%", opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: "100%", opacity: 0 }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
            className="fixed right-0 top-0 bottom-0 z-50 flex flex-col"
            style={{ width: "min(480px, 100vw)", background: "#0c111a", borderLeft: "1px solid rgba(255,255,255,0.09)" }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border)]">
              <div className="flex items-center gap-2">
                <Zap size={16} color="#00F0B5" />
                <span className="font-mono text-sm font-bold text-slate-100 uppercase tracking-wider">Trade Window</span>
              </div>
              <button onClick={onClose}
                className="p-1.5 rounded hover:bg-white/[0.06] text-slate-500 hover:text-slate-200 transition-colors">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto">
              {/* Symbol selector */}
              <div className="px-5 pt-4 pb-3">
                <div className="relative">
                  <button
                    onClick={() => setSymOpen((o) => !o)}
                    className="w-full flex items-center justify-between px-4 py-2.5 term-well hover:border-[var(--border-accent)] transition-all"
                  >
                    <span className="font-mono font-bold text-base text-slate-100">{symbol}</span>
                    <ChevronDown size={14} className={`text-slate-400 transition-transform ${symOpen ? "rotate-180" : ""}`} />
                  </button>
                  <AnimatePresence>
                    {symOpen && (
                      <motion.div
                        initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
                        className="absolute left-0 right-0 top-full mt-1 z-20 term-card p-2 grid grid-cols-4 gap-1"
                      >
                        {UNIVERSE.map((s) => (
                          <button key={s} onClick={() => { setSymbol(s); setSymOpen(false); setFill(null); handleReset(); }}
                            className={`px-2 py-1.5 rounded text-xs font-mono font-bold transition-all text-center ${s === symbol ? "text-[#00F0B5] bg-[rgba(0,240,181,0.12)]" : "text-slate-400 hover:text-slate-100 hover:bg-white/[0.05]"}`}>
                            {s}
                          </button>
                        ))}
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </div>

              {/* Quote strip */}
              <div className="px-5 pb-4">
                <div className="term-well p-3">
                  <div className="flex items-start justify-between mb-3">
                    <div>
                      <div className="text-2xl font-mono font-bold tabular" style={{ color }}>
                        ${fmt(quote.last)}
                      </div>
                      <div className="flex items-center gap-1.5 mt-0.5">
                        {chg >= 0
                          ? <TrendingUp size={12} style={{ color }} />
                          : <TrendingDown size={12} style={{ color }} />}
                        <span className="text-sm font-mono tabular font-semibold" style={{ color }}>
                          {chg >= 0 ? "+" : ""}{fmt(chg, 2)}%
                        </span>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-[10px] font-mono uppercase text-slate-600 mb-1">IV</div>
                      <div className="text-sm font-mono text-slate-300">{quote.iv ? `${(quote.iv * 100).toFixed(1)}%` : "—"}</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-2 text-xs font-mono">
                    {[["Bid", fmt(quote.bid)], ["Ask", fmt(quote.ask)], ["Volume", fmtVol(quote.volume)]].map(([l, v]) => (
                      <div key={l} className="rounded p-2" style={{ background: "rgba(255,255,255,0.03)" }}>
                        <div className="text-[9px] uppercase text-slate-600 mb-0.5">{l}</div>
                        <div className="text-slate-200 tabular">{l === "Volume" ? v : `$${v}`}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Sparkline */}
              <div className="px-5 pb-4">
                <div className="h-28 term-well p-2">
                  {barsLoading
                    ? <div className="h-full animate-pulse bg-white/[0.03] rounded" />
                    : <SparkChart bars={barsData?.bars} color={color} />
                  }
                </div>
              </div>

              {/* Bot signal pill */}
              {quote.iv && (
                <div className="px-5 pb-4">
                  <div className="flex items-center gap-3 px-3 py-2.5 rounded-md"
                       style={{ background: "rgba(157,78,221,0.1)", border: "1px solid rgba(157,78,221,0.2)" }}>
                    <Activity size={14} color="#9D4EDD" className="shrink-0" />
                    <div className="text-[11px] font-mono text-slate-300 leading-snug">
                      <span style={{ color: "#9D4EDD" }}>BOT SIGNAL</span>
                      {" · "}IV {(quote.iv * 100).toFixed(1)}% · Trend {quote.trend >= 0 ? "↑" : "↓"} {fmt(Math.abs(quote.trend), 2)}%
                      {" · "}
                      <span style={{ color: quote.iv > 0.3 ? "#00F0B5" : "#FFB800" }}>
                        {quote.iv > 0.3 ? "HIGH VOL — credit spread candidate" : "LOW VOL — proceed with caution"}
                      </span>
                    </div>
                  </div>
                </div>
              )}

              {/* Divider */}
              <div className="mx-5 border-t border-[var(--border)] mb-4" />

              {/* Order Form or Fill confirmation */}
              <div className="px-5 pb-6">
                {fill ? (
                  <motion.div
                    initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
                    className="rounded-lg p-5 text-center"
                    style={{ background: fill.status === "filled" ? "rgba(0,240,181,0.08)" : "rgba(255,184,0,0.08)", border: `1px solid ${fill.status === "filled" ? "rgba(0,240,181,0.25)" : "rgba(255,184,0,0.25)"}` }}
                  >
                    {fill.status === "filled"
                      ? <ArrowUpCircle size={32} className="mx-auto mb-3" color="#00F0B5" />
                      : <AlertCircle size={32} className="mx-auto mb-3" color="#FFB800" />
                    }
                    <div className="text-lg font-mono font-bold mb-1"
                         style={{ color: fill.status === "filled" ? "#00F0B5" : "#FFB800" }}>
                      ORDER {fill.status?.toUpperCase()}
                    </div>
                    <div className="text-sm font-mono text-slate-400 mb-1">
                      {side.toUpperCase()} {qty} × {symbol}
                    </div>
                    <div className="text-xl font-mono font-bold text-slate-100 mb-4">
                      @ ${fmt(fill.filled_price)}
                    </div>
                    <div className="text-xs font-mono text-slate-600 mb-4">
                      Order ID: {fill.order_id}
                    </div>
                    <button onClick={handleReset}
                      className="w-full py-2.5 rounded font-mono text-sm font-bold text-[#06090e]"
                      style={{ background: "linear-gradient(135deg,#00F0B5,#00D4FF)" }}>
                      NEW ORDER
                    </button>
                  </motion.div>
                ) : (
                  <form onSubmit={handleSubmit} className="space-y-4">
                    <div className="text-[10px] font-mono uppercase tracking-wider text-slate-500 mb-3">
                      Place Order
                    </div>

                    {/* Side toggle */}
                    <div className="grid grid-cols-2 gap-2">
                      {["buy", "sell"].map((s) => (
                        <button key={s} type="button" onClick={() => setSide(s)}
                          className="py-3 rounded-md font-mono font-bold text-sm transition-all"
                          style={{
                            background: side === s
                              ? s === "buy" ? "rgba(0,240,181,0.15)" : "rgba(255,59,105,0.15)"
                              : "rgba(255,255,255,0.04)",
                            border: `1.5px solid ${side === s ? (s === "buy" ? "#00F0B5" : "#FF3B69") : "rgba(255,255,255,0.08)"}`,
                            color: side === s ? (s === "buy" ? "#00F0B5" : "#FF3B69") : "#64748b",
                          }}>
                          {s === "buy" ? <ArrowUpCircle size={14} className="inline mr-1.5 -mt-0.5" /> : <ArrowDownCircle size={14} className="inline mr-1.5 -mt-0.5" />}
                          {s.toUpperCase()}
                        </button>
                      ))}
                    </div>

                    {/* Order type + TIF */}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-[10px] font-mono uppercase text-slate-500 mb-1.5 block">Order Type</label>
                        <select value={orderType} onChange={(e) => setOrderType(e.target.value)}
                          className="w-full px-3 py-2 rounded font-mono text-sm text-slate-200 bg-[var(--well)] border border-[var(--border)] focus:outline-none focus:border-[var(--border-accent)]">
                          {ORDER_TYPES.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] font-mono uppercase text-slate-500 mb-1.5 block">Time in Force</label>
                        <select value={tif} onChange={(e) => setTif(e.target.value)}
                          className="w-full px-3 py-2 rounded font-mono text-sm text-slate-200 bg-[var(--well)] border border-[var(--border)] focus:outline-none focus:border-[var(--border-accent)]">
                          {TIF_OPTIONS.map((t) => <option key={t} value={t}>{t.toUpperCase()}</option>)}
                        </select>
                      </div>
                    </div>

                    {/* Qty */}
                    <div>
                      <label className="text-[10px] font-mono uppercase text-slate-500 mb-1.5 block">Quantity (shares)</label>
                      <input
                        type="number" min="1" step="1" value={qty} onChange={(e) => setQty(e.target.value)}
                        className="w-full px-3 py-2 rounded font-mono text-sm text-slate-100 bg-[var(--well)] border border-[var(--border)] focus:outline-none focus:border-[var(--border-accent)] tabular"
                        placeholder="100"
                      />
                    </div>

                    {/* Limit price */}
                    {orderType === "limit" && (
                      <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}>
                        <label className="text-[10px] font-mono uppercase text-slate-500 mb-1.5 block">Limit Price</label>
                        <input
                          type="number" step="0.01" value={limitPrice} onChange={(e) => setLimitPrice(e.target.value)}
                          className="w-full px-3 py-2 rounded font-mono text-sm text-slate-100 bg-[var(--well)] border border-[var(--border)] focus:outline-none focus:border-[var(--border-accent)] tabular"
                          placeholder={fmt(quote.last)}
                        />
                      </motion.div>
                    )}

                    {/* Estimated cost */}
                    <div className="flex items-center justify-between px-3 py-2 rounded"
                         style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                      <span className="text-[10px] font-mono text-slate-600 uppercase">Est. {side === "buy" ? "Cost" : "Proceeds"}</span>
                      <span className="text-sm font-mono tabular font-semibold text-slate-200">
                        ${estimatedCost() > 0 ? estimatedCost().toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—"}
                      </span>
                    </div>

                    {/* Submit */}
                    <motion.button
                      type="submit"
                      disabled={orderMut.isPending}
                      whileTap={{ scale: 0.97 }}
                      className="w-full py-3.5 rounded-md font-mono font-bold text-sm transition-all disabled:opacity-60"
                      style={{
                        background: side === "buy"
                          ? "linear-gradient(135deg,#00F0B5,#00D4FF)"
                          : "linear-gradient(135deg,#FF3B69,#FF6B9D)",
                        color: "#06090e",
                      }}>
                      {orderMut.isPending
                        ? "SUBMITTING…"
                        : `${side.toUpperCase()} ${qty || 0} × ${symbol}`}
                    </motion.button>

                    <p className="text-center text-[9px] font-mono text-slate-700">
                      ALPACA PAPER ACCOUNT · SIMULATED FILLS IN MOCK MODE
                    </p>
                  </form>
                )}
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};

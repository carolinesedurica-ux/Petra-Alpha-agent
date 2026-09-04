import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";

const fmt = (n, d = 2) => (n == null ? "—" : Number(n).toFixed(d));

const Chip = ({ sym, onClick }) => {
  const chg = sym.change_pct ?? 0;
  const up = chg > 0;
  const dn = chg < 0;
  const color = up ? "#00F0B5" : dn ? "#FF3B69" : "#94a3b8";
  const Icon = up ? TrendingUp : dn ? TrendingDown : Minus;

  return (
    <motion.button
      onClick={() => onClick(sym)}
      whileHover={{ scale: 1.04, y: -1 }}
      whileTap={{ scale: 0.97 }}
      className="flex items-center gap-2.5 px-3 py-2 term-well shrink-0 cursor-pointer hover:border-[var(--border-accent)] transition-all"
      style={{ minWidth: 148 }}
    >
      <div className="text-left">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-mono font-bold text-slate-100">{sym.symbol}</span>
          <Icon size={11} style={{ color }} />
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[11px] font-mono tabular text-slate-200">${fmt(sym.last)}</span>
          <span className="text-[10px] font-mono tabular font-semibold" style={{ color }}>
            {chg >= 0 ? "+" : ""}{fmt(chg, 2)}%
          </span>
        </div>
      </div>
      <div className="ml-auto text-right hidden sm:block">
        <div className="text-[9px] font-mono text-slate-600 uppercase">Bid/Ask</div>
        <div className="text-[10px] font-mono tabular text-slate-400">
          {fmt(sym.bid)} / {fmt(sym.ask)}
        </div>
      </div>
    </motion.button>
  );
};

export const MarketTickerStrip = ({ liveMarket, onSymbolClick }) => {
  const symbols = liveMarket?.symbols || [];

  return (
    <div className="relative border-b border-[var(--border)] bg-[#06090e]/80 backdrop-blur">
      {/* gradient fade edges */}
      <div className="pointer-events-none absolute left-0 top-0 bottom-0 w-8 z-10"
           style={{ background: "linear-gradient(to right, #06090e, transparent)" }} />
      <div className="pointer-events-none absolute right-0 top-0 bottom-0 w-8 z-10"
           style={{ background: "linear-gradient(to left, #06090e, transparent)" }} />

      <div className="overflow-x-auto hide-scrollbar">
        <div className="flex items-center gap-2 px-4 py-2" style={{ width: "max-content" }}>
          {/* market status pill */}
          {liveMarket?.market && (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full shrink-0"
                 style={{ background: liveMarket.market.open ? "rgba(0,240,181,0.12)" : "rgba(255,59,105,0.1)" }}>
              <span className="h-1.5 w-1.5 rounded-full inline-block"
                    style={{ background: liveMarket.market.open ? "#00F0B5" : "#FF3B69" }} />
              <span className="text-[9px] font-mono uppercase tracking-wider"
                    style={{ color: liveMarket.market.open ? "#00F0B5" : "#FF3B69" }}>
                {liveMarket.market.open ? "MARKET OPEN" : "MARKET CLOSED"}
              </span>
            </div>
          )}

          <div className="w-px h-5 bg-[var(--border)] mx-1 shrink-0" />

          {symbols.length === 0
            ? Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="h-12 w-36 term-well animate-pulse shrink-0 rounded" />
              ))
            : symbols.map((sym) => (
                <Chip key={sym.symbol} sym={sym} onClick={onSymbolClick} />
              ))}
        </div>
      </div>
    </div>
  );
};

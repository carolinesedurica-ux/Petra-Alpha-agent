import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, Wallet, ShieldAlert, Target, DollarSign } from "lucide-react";
import { fmtUsd, fmtPct, fmtNum } from "../lib/format";

const Metric = ({ testid, icon: Icon, label, value, sub, subColor, accent, delay }) => (
  <motion.div
    data-testid={testid}
    initial={{ opacity: 0, y: 12 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ delay, duration: 0.4 }}
    className="term-card p-4 relative overflow-hidden group">
    <div className="absolute right-0 top-0 h-full w-1 opacity-60" style={{ background: accent }} />
    <div className="flex items-center gap-2 mb-2">
      <Icon size={13} className="text-slate-500" />
      <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500">{label}</span>
    </div>
    <div className="text-2xl sm:text-[26px] font-bold font-mono tracking-tight tabular text-slate-100">{value}</div>
    {sub && <div className="text-xs font-mono mt-1 tabular" style={{ color: subColor }}>{sub}</div>}
  </motion.div>
);

export const MetricsRibbon = ({ account }) => {
  const a = account || {};
  const dayUp = (a.day_pnl || 0) >= 0;
  const totUp = (a.total_pnl || 0) >= 0;
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
      <Metric testid="metric-total-equity" icon={DollarSign} label="Total Equity" accent="#00F0B5"
        value={fmtUsd(a.equity, 2)}
        sub={`${totUp ? "▲" : "▼"} ${fmtUsd(a.total_pnl, 0)} (${fmtPct(a.total_pnl_pct)})`}
        subColor={totUp ? "#00F0B5" : "#FF3B69"} delay={0.02} />
      <Metric testid="metric-day-pnl" icon={dayUp ? TrendingUp : TrendingDown} label="Day P&L" accent={dayUp ? "#00F0B5" : "#FF3B69"}
        value={`${dayUp ? "+" : ""}${fmtUsd(a.day_pnl, 0)}`}
        sub={fmtPct(a.day_pnl_pct)} subColor={dayUp ? "#00F0B5" : "#FF3B69"} delay={0.06} />
      <Metric testid="metric-open-risk" icon={ShieldAlert} label="Open Risk" accent="#FFB800"
        value={fmtUsd(a.open_risk, 0)}
        sub={`${fmtNum(a.open_risk_pct, 1)}% of risk cap`} subColor="#FFB800" delay={0.1} />
      <Metric testid="metric-buying-power" icon={Wallet} label="Buying Power" accent="#00D4FF"
        value={fmtUsd(a.buying_power, 0)}
        sub={`${a.open_positions || 0} open positions`} subColor="#94a3b8" delay={0.14} />
      <Metric icon={Target} label="Win Rate" accent="#9D4EDD"
        value={`${fmtNum(a.win_rate, 1)}%`}
        sub={`${a.total_trades || 0} closed trades`} subColor="#94a3b8" delay={0.18} />
      <Metric icon={DollarSign} label="Cash Balance" accent="#64748b"
        value={fmtUsd(a.cash, 0)}
        sub={`base ${fmtUsd(a.initial_equity, 0)}`} subColor="#64748b" delay={0.22} />
    </div>
  );
};

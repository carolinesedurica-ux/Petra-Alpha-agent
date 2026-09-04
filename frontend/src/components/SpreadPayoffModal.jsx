import { useMemo, useState, useEffect } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, ReferenceDot } from "recharts";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { fmtUsd, fmtNum, strategyMeta, legLabel } from "../lib/format";

function payoffAt(S, legs, credit, contracts) {
  // value of spread at expiry (per share), then P&L = (credit - intrinsic) * 100 * contracts
  let intrinsic = 0;
  legs.forEach((l) => {
    const iv = l.option_type === "call" ? Math.max(0, S - l.strike) : Math.max(0, l.strike - S);
    intrinsic += l.side === "sell" ? -iv : iv;
  });
  // intrinsic is net value to holder of the position (short spread) => add credit
  return (credit + intrinsic) * 100 * contracts;
}

export const SpreadPayoffModal = ({ position, open, onOpenChange }) => {
  const p = position;
  const meta = p ? strategyMeta[p.strategy] || {} : {};

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => setMounted(true), 100);
      return () => clearTimeout(timer);
    } else {
      setMounted(false);
    }
  }, [open]);

  const { data, breakevens, maxProfit, maxLoss } = useMemo(() => {
    if (!p) return { data: [], breakevens: [], maxProfit: 0, maxLoss: 0 };
    const strikes = p.legs.map((l) => l.strike);
    const lo = Math.min(...strikes, p.entry_underlying) * 0.97;
    const hi = Math.max(...strikes, p.entry_underlying) * 1.03;
    const pts = [];
    let prev = null, bes = [];
    for (let i = 0; i <= 120; i++) {
      const S = lo + ((hi - lo) * i) / 120;
      const pnl = payoffAt(S, p.legs, p.credit, p.contracts);
      if (prev && Math.sign(prev.pnl) !== Math.sign(pnl)) bes.push(Number(S.toFixed(1)));
      pts.push({ S: Number(S.toFixed(2)), pnl: Number(pnl.toFixed(0)) });
      prev = { S, pnl };
    }
    const mp = Math.max(...pts.map((x) => x.pnl));
    const ml = Math.min(...pts.map((x) => x.pnl));
    return { data: pts, breakevens: bes, maxProfit: mp, maxLoss: ml };
  }, [p]);

  if (!p) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#0c111a] border border-[var(--border)] text-slate-200 max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-3">
            <span className="text-slate-100">{p.underlying}</span>
            <span className="px-2 py-0.5 rounded text-[10px] font-bold font-mono" style={{ color: meta.color, background: meta.bg }}>
              {meta.label} · {meta.bias}
            </span>
            <span className="text-xs font-mono text-slate-500">
              {p.legs.map((l) => legLabel(l)).join(" / ")} · x{p.contracts}
            </span>
          </DialogTitle>
        </DialogHeader>

        <div className="grid grid-cols-3 gap-2 mb-2">
          <div className="term-well p-2.5"><div className="text-[10px] font-mono text-slate-500 uppercase">Max Profit</div>
            <div className="font-mono font-bold text-[#00F0B5]">{fmtUsd(maxProfit, 0)}</div></div>
          <div className="term-well p-2.5"><div className="text-[10px] font-mono text-slate-500 uppercase">Max Loss</div>
            <div className="font-mono font-bold text-[#FF3B69]">{fmtUsd(maxLoss, 0)}</div></div>
          <div className="term-well p-2.5"><div className="text-[10px] font-mono text-slate-500 uppercase">Breakeven</div>
            <div className="font-mono font-bold text-slate-200">{breakevens.map((b) => fmtNum(b, 1)).join(" / ") || "—"}</div></div>
        </div>

        <div className="h-[260px] min-w-0 w-full" style={{ width: "100%", height: 260, minHeight: 260, position: "relative" }}>
          {mounted && (
            <ResponsiveContainer width="100%" height={260} minWidth={0} minHeight={260}>
            <LineChart data={data} margin={{ top: 8, right: 10, left: 0, bottom: 0 }}>
              <XAxis dataKey="S" tick={{ fill: "#475569", fontSize: 10, fontFamily: "JetBrains Mono" }}
                axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} minTickGap={40} />
              <YAxis tick={{ fill: "#475569", fontSize: 10, fontFamily: "JetBrains Mono" }} axisLine={false} tickLine={false}
                width={54} tickFormatter={(v) => `$${v}`} />
              <ReferenceLine y={0} stroke="#64748b" strokeOpacity={0.5} />
              <ReferenceLine x={data.reduce((c, d) => (Math.abs(d.S - p.entry_underlying) < Math.abs(c.S - p.entry_underlying) ? d : c)).S}
                stroke="#00D4FF" strokeDasharray="4 4" label={{ value: "spot", fill: "#00D4FF", fontSize: 10 }} />
              <Tooltip contentStyle={{ background: "#080c13", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, fontFamily: "JetBrains Mono", fontSize: 12 }}
                formatter={(v) => [fmtUsd(v, 0), "P&L @ expiry"]} labelFormatter={(l) => `${p.underlying} @ ${l}`} />
              <Line type="monotone" dataKey="pnl" stroke={meta.color} strokeWidth={2.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
          )}
        </div>
        <p className="text-[11px] font-mono text-slate-600 mt-1">
          Defined-risk payoff at expiry. TP target ${fmtNum(p.tp_target, 2)} · Stop ${fmtNum(p.stop_target, 2)} · Entry credit ${fmtNum(p.credit, 2)}
        </p>
      </DialogContent>
    </Dialog>
  );
};

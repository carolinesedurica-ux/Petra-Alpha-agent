import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { fmtUsd } from "../lib/format";

export const EquityChart = ({ pnl, initialEquity = 100000 }) => {
  const data = useMemo(
    () =>
      (pnl || []).map((s, i) => ({
        i,
        equity: s.equity,
        ts: new Date(s.ts).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit" }),
      })),
    [pnl]
  );
  const last = data.length ? data[data.length - 1].equity : initialEquity;
  const up = last >= initialEquity;
  const color = up ? "#00F0B5" : "#FF3B69";
  const min = Math.min(initialEquity, ...data.map((d) => d.equity));
  const max = Math.max(initialEquity, ...data.map((d) => d.equity));

  return (
    <div className="term-card p-4 h-full flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Equity Growth Curve</h3>
          <p className="text-[11px] font-mono text-slate-600 mt-0.5">$100K paper account // marked-to-model</p>
        </div>
        <div className="text-right">
          <div className="text-lg font-bold font-mono tabular" style={{ color }}>{fmtUsd(last, 0)}</div>
          <div className="text-[11px] font-mono" style={{ color }}>
            {up ? "▲" : "▼"} {fmtUsd(last - initialEquity, 0)}
          </div>
        </div>
      </div>
      <div className="flex-1 min-h-[240px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="ts" tick={{ fill: "#475569", fontSize: 10, fontFamily: "JetBrains Mono" }}
              axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} minTickGap={60} />
            <YAxis domain={[min * 0.999, max * 1.001]} tick={{ fill: "#475569", fontSize: 10, fontFamily: "JetBrains Mono" }}
              axisLine={false} tickLine={false} width={62}
              tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
            <ReferenceLine y={initialEquity} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.5} />
            <Tooltip
              contentStyle={{ background: "#0c111a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, fontFamily: "JetBrains Mono", fontSize: 12 }}
              labelStyle={{ color: "#94a3b8" }} formatter={(v) => [fmtUsd(v, 2), "Equity"]} />
            <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={2} fill="url(#eq)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

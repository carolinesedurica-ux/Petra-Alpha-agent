import { useMemo, useState, useEffect } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { fmtUsd } from "../lib/format";

export const EquityChart = ({ pnl, initialEquity = 100000 }) => {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const data = useMemo(() => {
    const raw = (pnl || []).map((s, i) => ({
      i,
      equity: s.equity,
      benchmark: s.benchmark ?? null,
      ts: new Date(s.ts).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }),
    }));

    if (raw.length === 0) {
      return [
        { i: 0, equity: initialEquity, benchmark: initialEquity, ts: "Open" },
        { i: 1, equity: initialEquity, benchmark: initialEquity, ts: "Live" }
      ];
    }

    if (raw.length === 1) {
      return [
        { i: 0, equity: initialEquity, benchmark: initialEquity, ts: "Baseline" },
        { i: 1, equity: raw[0].equity, benchmark: raw[0].benchmark ?? initialEquity, ts: raw[0].ts }
      ];
    }

    return raw;
  }, [pnl, initialEquity]);

  const last = data.length ? data[data.length - 1].equity : initialEquity;
  const up = last >= initialEquity;
  const color = up ? "#00F0B5" : "#FF3B69";
  const bench = data.filter((d) => d.benchmark != null);
  const lastBench = bench.length ? bench[bench.length - 1].benchmark : null;
  const vals = [...data.map((d) => d.equity), ...bench.map((d) => d.benchmark)];
  const rawMin = Math.min(initialEquity, ...vals);
  const rawMax = Math.max(initialEquity, ...vals);
  const spread = Math.max(200, rawMax - rawMin);
  const domainMin = Math.floor(rawMin - spread * 0.15);
  const domainMax = Math.ceil(rawMax + spread * 0.15);

  return (
    <div data-testid="equity-chart-card" className="term-card p-4 flex-1 flex flex-col min-h-[340px]">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Equity Growth Curve</h3>
          <p className="text-[11px] font-mono text-slate-600 mt-0.5">
            <span style={{ color }}>■</span> Petra equity&nbsp;&nbsp;<span className="text-slate-500">┄</span> SPY buy &amp; hold
          </p>
        </div>
        <div className="text-right">
          <div className="text-lg font-bold font-mono tabular" style={{ color }}>{fmtUsd(last, 0)}</div>
          <div className="text-[11px] font-mono" style={{ color }}>
            {up ? "▲" : "▼"} {fmtUsd(last - initialEquity, 0)}
          </div>
          {lastBench != null && (
            <div data-testid="benchmark-edge" className="text-[10px] font-mono text-slate-500 mt-0.5">
              vs SPY {fmtUsd(lastBench, 0)} · edge <span style={{ color: last - lastBench >= 0 ? "#00F0B5" : "#FF3B69" }}>{last - lastBench >= 0 ? "+" : ""}{fmtUsd(last - lastBench, 0)}</span>
            </div>
          )}
        </div>
      </div>
      <div className="flex-1 min-h-[240px] min-w-0 relative w-full" style={{ width: "100%", height: 240, minHeight: 240 }}>
        {mounted && (
          <ResponsiveContainer width="100%" height={240} minWidth={0} minHeight={240}>
          <AreaChart data={data} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                <stop offset="100%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="ts" tick={{ fill: "#475569", fontSize: 10, fontFamily: "JetBrains Mono" }}
              axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} minTickGap={50} />
            <YAxis domain={[domainMin, domainMax]} tick={{ fill: "#475569", fontSize: 10, fontFamily: "JetBrains Mono" }}
              axisLine={false} tickLine={false} width={62}
              tickFormatter={(v) => `$${(v / 1000).toFixed(1)}k`} />
            <ReferenceLine y={initialEquity} stroke="#64748b" strokeDasharray="4 4" strokeOpacity={0.5} />
            <Tooltip
              contentStyle={{ background: "#0c111a", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 6, fontFamily: "JetBrains Mono", fontSize: 12 }}
              labelStyle={{ color: "#94a3b8" }} formatter={(v, n) => [fmtUsd(v, 2), n === "benchmark" ? "SPY B&H" : "Equity"]} />
            <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={2} fill="url(#eq)" />
            <Area type="monotone" dataKey="benchmark" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="5 4"
              fill="none" fillOpacity={0} connectNulls dot={false} activeDot={{ r: 3 }} />
          </AreaChart>
        </ResponsiveContainer>
        )}
      </div>
    </div>
  );
};

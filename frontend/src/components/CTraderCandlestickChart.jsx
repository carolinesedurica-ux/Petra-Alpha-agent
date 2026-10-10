import { useMemo } from "react";

const fmt = (value) => {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "—";
};

const timeLabel = (seconds) => {
  const n = Number(seconds);
  if (!Number.isFinite(n)) return "—";
  return new Date(n * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};

const pnlLabel = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return `${n >= 0 ? "+" : ""}$${n.toFixed(2)}`;
};

export const CTraderCandlestickChart = ({ candles = [], position, marketPrice, barPeriod = "M5", updatedAt }) => {
  const chart = useMemo(() => {
    const all = (candles || []).filter((c) =>
      [c?.open, c?.high, c?.low, c?.close].every((v) => Number.isFinite(Number(v)))
    );
    if (!all.length) return null;

    const entryIndexes = [];
    const exitIndexes = [];
    all.forEach((c, i) => {
      (c?.markers || []).forEach((m) => {
        if (m?.kind === "ENTRY") entryIndexes.push(i);
        if (m?.kind === "EXIT") exitIndexes.push(i);
      });
    });

    let focusMode = "MARKET";
    let focusIndex = all.length - 1;
    let focusStart = Math.max(0, all.length - 48);
    let focusEnd = all.length;

    if (position && entryIndexes.length) {
      focusMode = "ACTIVE TRADE";
      focusIndex = entryIndexes[entryIndexes.length - 1];
      focusStart = Math.max(0, focusIndex - 24);
      focusEnd = Math.min(all.length, Math.max(focusIndex + 24, all.length));
    } else if (exitIndexes.length) {
      focusMode = "LATEST COMPLETED TRADE";
      const exitIndex = exitIndexes[exitIndexes.length - 1];
      const priorEntries = entryIndexes.filter((i) => i <= exitIndex);
      const entryIndex = priorEntries.length ? priorEntries[priorEntries.length - 1] : exitIndex;
      const lo = Math.min(entryIndex, exitIndex);
      const hi = Math.max(entryIndex, exitIndex);
      focusIndex = exitIndex;
      focusStart = Math.max(0, lo - 16);
      focusEnd = Math.min(all.length, hi + 17);
    }

    const valid = all.slice(focusStart, focusEnd);
    const overlay = [position?.entry_price, position?.stop_loss, position?.take_profit, marketPrice]
      .map(Number)
      .filter(Number.isFinite);
    const markerPrices = valid.flatMap((c) => (c?.markers || []).map((m) => Number(m?.price)).filter(Number.isFinite));
    const lows = valid.map((c) => Number(c.low));
    const highs = valid.map((c) => Number(c.high));
    const rawMin = Math.min(...lows, ...overlay, ...markerPrices);
    const rawMax = Math.max(...highs, ...overlay, ...markerPrices);
    const span = Math.max(rawMax - rawMin, rawMax * 0.0005, 1);
    const min = rawMin - span * 0.08;
    const max = rawMax + span * 0.08;

    return { valid, min, max, focusMode, focusIndex, focusStart };
  }, [candles, marketPrice, position, position?.entry_price, position?.stop_loss, position?.take_profit]);

  if (!chart) {
    return (
      <div className="term-well h-[320px] flex items-center justify-center text-sm font-mono text-slate-500">
        Waiting for cTrader {barPeriod} candles…
      </div>
    );
  }

  const W = 1000;
  const H = 360;
  const left = 26;
  const right = 84;
  const top = 42;
  const bottom = 38;
  const plotW = W - left - right;
  const plotH = H - top - bottom;
  const y = (price) => top + ((chart.max - Number(price)) / (chart.max - chart.min)) * plotH;
  const step = plotW / chart.valid.length;
  const bodyW = Math.max(3, Math.min(12, step * 0.58));

  const levels = [
    { label: "MARK", value: marketPrice, stroke: "#94a3b8" },
    { label: "ENTRY", value: position?.entry_price, stroke: "#38bdf8" },
    { label: "SL", value: position?.stop_loss, stroke: "#fb7185" },
    { label: "TP", value: position?.take_profit, stroke: "#34d399" },
  ].filter((l) => Number.isFinite(Number(l.value)));

  const gridPrices = Array.from({ length: 5 }, (_, i) => chart.max - ((chart.max - chart.min) * i) / 4);

  return (
    <div className="term-well overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="font-mono text-xs text-slate-200">US500 · {barPeriod} CANDLES</div>
          <div className="font-mono text-[10px] text-slate-500 mt-1">Same cTrader bars used by Petra's decision engine · entries/exits overlaid</div>
        </div>
        <div className="font-mono text-xs text-slate-400 flex gap-4 flex-wrap">
          <span>FOCUS <span className="text-[#00F0B5]">{chart.focusMode}</span></span>
          <span>MARK <span className="text-slate-100">{fmt(marketPrice)}</span></span>
          <span>UPDATED <span className="text-slate-100">{timeLabel(updatedAt)}</span></span>
        </div>
      </div>
      <div className="w-full overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[760px] h-auto block" role="img" aria-label="US500 cTrader candlestick chart with Petra trade markers">
          <rect x="0" y="0" width={W} height={H} fill="#090e16" />
          <text x={left} y="24" fill="#64748b" fontSize="10" fontFamily="monospace">
            VALIDATION VIEW · {chart.focusMode}
          </text>

          {gridPrices.map((price, i) => {
            const gy = y(price);
            return (
              <g key={`grid-${i}`}>
                <line x1={left} x2={W - right} y1={gy} y2={gy} stroke="rgba(148,163,184,0.10)" strokeWidth="1" />
                <text x={W - right + 8} y={gy + 4} fill="#64748b" fontSize="11" fontFamily="monospace">{fmt(price)}</text>
              </g>
            );
          })}

          {chart.valid.map((c, i) => {
            const cx = left + step * i + step / 2;
            const o = Number(c.open);
            const h = Number(c.high);
            const l = Number(c.low);
            const cl = Number(c.close);
            const up = cl >= o;
            const color = up ? "#00F0B5" : "#FF5D7D";
            const bodyTop = y(Math.max(o, cl));
            const bodyBottom = y(Math.min(o, cl));
            const bodyH = Math.max(1.5, bodyBottom - bodyTop);
            const markers = c?.markers || [];
            return (
              <g key={`${c.time || i}-${i}`}>
                <line x1={cx} x2={cx} y1={y(h)} y2={y(l)} stroke={color} strokeWidth="1.2" opacity="0.9" />
                <rect x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={up ? "rgba(0,240,181,0.38)" : "rgba(255,93,125,0.42)"} stroke={color} strokeWidth="1" />
                {markers.map((m, markerIndex) => {
                  const price = Number(m?.price);
                  if (!Number.isFinite(price)) return null;
                  const isEntry = m.kind === "ENTRY";
                  const markerColor = isEntry ? "#38bdf8" : Number(m?.pnl) >= 0 ? "#34d399" : "#fb7185";
                  const my = y(price);
                  const direction = isEntry ? -1 : 1;
                  const tipY = my;
                  const baseY = my + direction * 12;
                  const textY = my + direction * 24;
                  const label = isEntry
                    ? `${m.side || ""} ENTRY ${fmt(price)}`
                    : `EXIT ${fmt(price)} ${pnlLabel(m?.pnl)}`.trim();
                  return (
                    <g key={`${m.kind}-${m.time || markerIndex}-${markerIndex}`}>
                      <polygon points={`${cx},${tipY} ${cx - 5},${baseY} ${cx + 5},${baseY}`} fill={markerColor} opacity="0.95" />
                      <text x={cx} y={textY} fill={markerColor} fontSize="9" textAnchor="middle" fontFamily="monospace">{label}</text>
                    </g>
                  );
                })}
              </g>
            );
          })}

          {levels.map((level) => {
            const ly = y(level.value);
            return (
              <g key={level.label}>
                <line x1={left} x2={W - right} y1={ly} y2={ly} stroke={level.stroke} strokeWidth="1" strokeDasharray={level.label === "MARK" ? "3 5" : "7 5"} opacity="0.9" />
                <rect x={W - right + 2} y={ly - 10} width="77" height="20" rx="3" fill="#0c111a" stroke={level.stroke} strokeWidth="0.8" />
                <text x={W - right + 7} y={ly + 4} fill={level.stroke} fontSize="10" fontFamily="monospace">{level.label} {fmt(level.value)}</text>
              </g>
            );
          })}

          {[0, Math.floor(chart.valid.length / 2), chart.valid.length - 1].map((idx) => {
            const c = chart.valid[idx];
            if (!c) return null;
            const x = left + step * idx + step / 2;
            return <text key={`time-${idx}`} x={x} y={H - 14} fill="#64748b" fontSize="10" textAnchor="middle" fontFamily="monospace">{timeLabel(c.time)}</text>;
          })}
        </svg>
      </div>
    </div>
  );
};

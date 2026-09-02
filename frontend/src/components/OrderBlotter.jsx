import { strategyMeta, timeAgo } from "../lib/format";

const statusMeta = {
  filled: { c: "#00F0B5", l: "FILLED" },
  canceled: { c: "#FFB800", l: "CANCELED" },
  rejected: { c: "#FF3B69", l: "REJECTED" },
  expired: { c: "#64748b", l: "EXPIRED" },
};

const legLabel = (l) => {
  const m = l.symbol?.match(/^([A-Z]+)(\d{6})([CP])(\d{8})$/);
  if (!m) return l.symbol;
  return `${l.side === "sell" ? "S" : "B"} ${parseInt(m[4], 10) / 1000}${m[3]}`;
};

export const OrderBlotter = ({ orders }) => (
  <div className="overflow-x-auto">
    <table data-testid="order-blotter-table" className="w-full min-w-[900px]">
      <thead>
        <tr className="text-[10px] font-mono uppercase tracking-wider text-slate-500 border-b border-[var(--border)]">
          {["Time", "Intent", "Underlying", "Strategy", "Legs", "Qty", "Type", "Limit", "Fill", "Status", "Alpaca ID"].map((h) => (
            <th key={h} className="text-left px-3 py-2 font-medium whitespace-nowrap">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {(orders || []).map((o) => {
          const meta = strategyMeta[o.strategy] || {};
          const sm = statusMeta[o.status] || { c: "#94a3b8", l: (o.status || "").toUpperCase() };
          const isOpen = o.intent === "open";
          return (
            <tr key={o.id} data-testid={`order-row-${o.id}`} className="border-b border-[var(--border)] hover:bg-white/[0.02] text-xs font-mono">
              <td className="px-3 py-2.5 text-slate-500 whitespace-nowrap">{timeAgo(o.ts)}</td>
              <td className="px-3 py-2.5">
                <span className="text-[10px] font-bold" style={{ color: isOpen ? "#00D4FF" : "#FFB800" }}>
                  {isOpen ? "OPEN" : `CLOSE${o.reason ? ` · ${o.reason.replace("_", " ").toUpperCase()}` : ""}`}
                </span>
              </td>
              <td className="px-3 py-2.5 font-bold text-slate-100">{o.underlying}</td>
              <td className="px-3 py-2.5">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold" style={{ color: meta.color, background: meta.bg }}>{meta.label || o.strategy}</span>
              </td>
              <td className="px-3 py-2.5 text-slate-400 whitespace-nowrap">{(o.legs || []).map(legLabel).join("  ")}</td>
              <td className="px-3 py-2.5 text-slate-400 tabular">x{o.qty}</td>
              <td className="px-3 py-2.5 text-slate-400 uppercase">{o.order_type}</td>
              <td className="px-3 py-2.5 text-slate-400 tabular">{o.limit_price != null ? `$${Number(o.limit_price).toFixed(2)}` : "MKT"}</td>
              <td className="px-3 py-2.5 tabular text-slate-200">{o.filled_price ? `$${Number(o.filled_price).toFixed(2)}` : "—"}</td>
              <td className="px-3 py-2.5"><span className="text-[10px] font-bold" style={{ color: sm.c }}>{sm.l}</span></td>
              <td className="px-3 py-2.5 text-slate-600 truncate max-w-[140px]" title={o.alpaca_order_id}>{o.alpaca_order_id?.slice(0, 8) || "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
    {(!orders || orders.length === 0) && (
      <div data-testid="order-blotter-empty" className="px-4 py-8 text-center text-slate-600 font-mono text-xs">No orders routed yet</div>
    )}
  </div>
);

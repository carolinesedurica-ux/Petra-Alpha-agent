import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Radio, CheckCircle2, XCircle, Clock, Cpu } from "lucide-react";

// Synthetic bot events derived from real decisions data
const buildFeed = (decisions) => {
  const events = [];
  (decisions || []).slice(0, 20).forEach((d, i) => {
    const ts = d.created_at;
    // scan event
    events.push({
      id: `scan-${d.id}`,
      type: "scan",
      symbol: d.underlying,
      ts,
      msg: `Scanning ${d.underlying} — IV ${d.verdict?.regime || "neutral"} · confidence ${d.verdict ? Math.round(d.verdict.confidence * 100) : "—"}%`,
    });
    // verdict event
    events.push({
      id: `verdict-${d.id}`,
      type: d.outcome,
      symbol: d.underlying,
      ts,
      msg: d.reason || `${d.outcome.toUpperCase()} — ${d.strategy || "strategy"}`,
      strategy: d.strategy,
    });
  });
  return events.reverse();
};

const typeStyle = {
  scan:     { color: "#00D4FF", bg: "rgba(0,212,255,0.08)", Icon: Cpu,          label: "SCAN"    },
  approved: { color: "#00F0B5", bg: "rgba(0,240,181,0.08)", Icon: CheckCircle2, label: "ENTRY"   },
  rejected: { color: "#FF3B69", bg: "rgba(255,59,105,0.08)", Icon: XCircle,     label: "SKIP"    },
  skipped:  { color: "#475569", bg: "rgba(71,85,105,0.10)",  Icon: Clock,        label: "IDLE"    },
  error:    { color: "#FFB800", bg: "rgba(255,184,0,0.08)",  Icon: XCircle,     label: "ERROR"   },
};

const FeedRow = ({ ev }) => {
  const style = typeStyle[ev.type] || typeStyle.skipped;
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      className="flex gap-2.5 py-2 px-3 border-b border-[var(--border)] hover:bg-white/[0.015] transition-colors"
    >
      <style.Icon size={12} style={{ color: style.color }} className="mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 mb-0.5">
          <span className="text-[10px] font-mono font-bold text-slate-100">{ev.symbol}</span>
          <span className="text-[9px] px-1 py-0.5 rounded font-mono font-bold uppercase"
                style={{ color: style.color, background: style.bg }}>
            {style.label}
          </span>
        </div>
        <p className="text-[10px] font-mono text-slate-500 leading-snug truncate" title={ev.msg}>
          {ev.msg}
        </p>
      </div>
    </motion.div>
  );
};

export const BotActivityFeed = ({ decisions }) => {
  const feed = buildFeed(decisions);
  const scrollRef = useRef(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [isLive, setIsLive] = useState(true);

  // Blink live indicator
  useEffect(() => {
    const t = setInterval(() => setIsLive((v) => !v), 900);
    return () => clearInterval(t);
  }, []);

  // Auto-scroll to bottom when new events arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [feed.length, autoScroll]);

  return (
    <div className="term-card flex flex-col overflow-hidden" style={{ height: 340 }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)] shrink-0">
        <div className="flex items-center gap-2">
          <Radio size={13} style={{ color: isLive ? "#00F0B5" : "#475569" }} />
          <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Bot Activity Feed</h3>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-slate-600">{feed.length} events</span>
          <button
            onClick={() => setAutoScroll((v) => !v)}
            className={`text-[9px] px-1.5 py-0.5 rounded font-mono uppercase transition-colors ${autoScroll ? "text-[#00D4FF] bg-[rgba(0,212,255,0.1)]" : "text-slate-600 bg-white/[0.04]"}`}>
            {autoScroll ? "AUTO" : "PAUSED"}
          </button>
        </div>
      </div>

      <div ref={scrollRef}
           onScroll={(e) => {
             const el = e.target;
             const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30;
             setAutoScroll(atBottom);
           }}
           className="flex-1 overflow-y-auto">
        {feed.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-600 font-mono text-xs">
            <Cpu size={20} className="mb-2 opacity-40" />
            <span>Run an agent cycle to start</span>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {feed.map((ev) => <FeedRow key={ev.id} ev={ev} />)}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
};

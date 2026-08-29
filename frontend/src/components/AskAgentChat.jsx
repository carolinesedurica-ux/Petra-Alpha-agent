import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { Send, Sparkles, Bot, User } from "lucide-react";
import { streamChat } from "../lib/api";

const SUGGESTIONS = [
  "Why did you open the current positions?",
  "What is our total portfolio risk right now?",
  "Simulate a 2% SPY drop — how are we exposed?",
  "Which risk gate rejects trades most often?",
];

export const AskAgentChat = ({ height = "540px" }) => {
  const [messages, setMessages] = useState([
    { role: "agent", text: "Options Alpha Agent online. Ask me about my positions, reasoning, or risk posture." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sessionId = useRef("operator-" + Math.random().toString(36).slice(2, 8));
  const listRef = useRef(null);

  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages]);

  const send = async (text) => {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", text: q }, { role: "agent", text: "" }]);
    try {
      await streamChat(q, sessionId.current, (chunk) => {
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { role: "agent", text: copy[copy.length - 1].text + chunk };
          return copy;
        });
      });
    } catch (e) {
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { role: "agent", text: "[connection error — try again]" };
        return copy;
      });
    }
    setBusy(false);
  };

  return (
    <div className="term-card flex flex-col overflow-hidden" style={{ height }}>
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[var(--border)]">
        <Sparkles size={14} className="text-[#00D4FF]" />
        <h3 className="text-sm font-mono uppercase tracking-wider text-slate-400">Ask The Agent</h3>
        <span className="ml-auto text-[10px] font-mono text-slate-600">MCP · CLAUDE 4.6</span>
      </div>

      <div ref={listRef} data-testid="agent-chat-messages" className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((m, i) => (
          <motion.div key={i} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
            className={`flex gap-2 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
            <div className="shrink-0 h-6 w-6 rounded-md flex items-center justify-center border border-[var(--border)]"
              style={{ background: m.role === "user" ? "rgba(0,212,255,0.1)" : "rgba(0,240,181,0.1)" }}>
              {m.role === "user" ? <User size={12} className="text-[#00D4FF]" /> : <Bot size={12} className="text-[#00F0B5]" />}
            </div>
            <div className={`max-w-[82%] px-3 py-2 rounded-lg text-[13px] leading-relaxed whitespace-pre-wrap ${
              m.role === "user" ? "bg-[#00D4FF]/[0.08] text-slate-200" : "term-well text-slate-300"}`}>
              {m.text || <span className="inline-block w-2 h-3 bg-[#00F0B5] animate-pulse" />}
            </div>
          </motion.div>
        ))}
        {messages.length <= 1 && (
          <div className="space-y-1.5 pt-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} onClick={() => send(s)}
                className="w-full text-left text-[11px] font-mono px-2.5 py-1.5 term-well text-slate-400 hover:text-[#00D4FF] hover:border-[#00D4FF]/40 transition-colors">
                › {s}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="p-3 border-t border-[var(--border)]">
        <div className="flex items-center gap-2">
          <input
            data-testid="agent-chat-input" value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()} placeholder="Ask about positions, risk, reasoning…"
            className="flex-1 bg-[var(--well)] border border-[var(--border)] rounded-md px-3 py-2 text-[13px] font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-[#00D4FF]/50" />
          <button data-testid="agent-chat-submit" onClick={() => send()} disabled={busy}
            className="h-9 w-9 rounded-md flex items-center justify-center text-[#06090e] disabled:opacity-50"
            style={{ background: "linear-gradient(135deg,#00D4FF,#00F0B5)" }}>
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  );
};

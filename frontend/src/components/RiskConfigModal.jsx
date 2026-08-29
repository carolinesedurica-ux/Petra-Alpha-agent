import { useState, useEffect } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "./ui/dialog";
import { Slider } from "./ui/slider";
import { ShieldCheck } from "lucide-react";

const FIELDS = [
  { key: "max_risk_pct", label: "Max Risk / Trade", min: 0.5, max: 5, step: 0.25, suffix: "%" },
  { key: "max_concurrent", label: "Max Concurrent Positions", min: 1, max: 10, step: 1, suffix: "" },
  { key: "min_credit_width", label: "Min Credit-to-Width", min: 0.1, max: 0.5, step: 0.02, suffix: "×", scale: 1 },
  { key: "target_delta", label: "Target Short Delta", min: 0.1, max: 0.4, step: 0.01, suffix: "Δ" },
  { key: "dte_min", label: "Min DTE", min: 1, max: 7, step: 1, suffix: "d" },
  { key: "tp_pct", label: "Take-Profit (% of credit)", min: 0.25, max: 0.9, step: 0.05, suffix: "×" },
  { key: "stop_mult", label: "Stop-Loss (× credit)", min: 1.5, max: 4, step: 0.25, suffix: "×" },
];

const PRESETS = {
  conservative: { max_risk_pct: 1, max_concurrent: 3, min_credit_width: 0.2, target_delta: 0.16, dte_min: 3, tp_pct: 0.5, stop_mult: 2, aggressiveness: "conservative" },
  balanced: { max_risk_pct: 2, max_concurrent: 5, min_credit_width: 0.18, target_delta: 0.22, dte_min: 3, tp_pct: 0.5, stop_mult: 2, aggressiveness: "balanced" },
  aggressive: { max_risk_pct: 3, max_concurrent: 8, min_credit_width: 0.15, target_delta: 0.3, dte_min: 2, tp_pct: 0.6, stop_mult: 2.5, aggressiveness: "aggressive" },
};

export const RiskConfigModal = ({ open, onOpenChange, config, onSave }) => {
  const [draft, setDraft] = useState(config || {});
  useEffect(() => { if (config) setDraft(config); }, [config, open]);

  const set = (k, v) => setDraft((d) => ({ ...d, [k]: v }));
  const fmtVal = (f) => (f.suffix === "%" ? draft[f.key] : f.key.includes("pct") || f.key.includes("width") || f.key === "target_delta" || f.suffix === "×" ? Number(draft[f.key]).toFixed(2) : draft[f.key]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#0c111a] border border-[var(--border)] text-slate-200 max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-display flex items-center gap-2">
            <ShieldCheck size={18} className="text-[#00F0B5]" /> Deterministic Risk Engine
          </DialogTitle>
        </DialogHeader>

        <div className="flex gap-2 mb-1">
          {Object.keys(PRESETS).map((p) => (
            <button key={p} onClick={() => setDraft((d) => ({ ...d, ...PRESETS[p] }))}
              className={`flex-1 py-1.5 rounded text-[11px] font-mono uppercase tracking-wider transition-colors ${
                draft.aggressiveness === p ? "text-[#06090e] font-bold" : "term-well text-slate-400 hover:text-white"}`}
              style={draft.aggressiveness === p ? { background: "linear-gradient(135deg,#00F0B5,#00D4FF)" } : {}}>
              {p}
            </button>
          ))}
        </div>

        <div className="space-y-4 max-h-[52vh] overflow-y-auto pr-1 py-2">
          {FIELDS.map((f) => (
            <div key={f.key}>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-mono text-slate-400">{f.label}</span>
                <span className="text-xs font-mono font-bold text-[#00F0B5] tabular">{fmtVal(f)}{f.suffix}</span>
              </div>
              <Slider value={[Number(draft[f.key] ?? f.min)]} min={f.min} max={f.max} step={f.step}
                onValueChange={([v]) => set(f.key, v)} className="cursor-pointer" />
            </div>
          ))}
        </div>

        <button data-testid="risk-config-save-btn"
          onClick={() => { onSave(draft); onOpenChange(false); }}
          className="mt-2 w-full py-2.5 rounded-md font-mono text-xs font-bold text-[#06090e]"
          style={{ background: "linear-gradient(135deg,#00F0B5,#00D4FF)" }}>
          APPLY RISK PARAMETERS
        </button>
      </DialogContent>
    </Dialog>
  );
};

import { useState } from "react";
import "@/App.css";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  getAccount, getPositions, getTrades, getDecisions, getPnl, getStatus,
  getConfig, updateConfig, runCycle, pauseAgent, closePosition, getOrders, getModels,
} from "@/lib/api";
import { HeaderTerminal } from "@/components/HeaderTerminal";
import { MetricsRibbon } from "@/components/MetricsRibbon";
import { EquityChart } from "@/components/EquityChart";
import { PositionsTable } from "@/components/PositionsTable";
import { AgentReasoningPanel } from "@/components/AgentReasoningPanel";
import { AskAgentChat } from "@/components/AskAgentChat";
import { RiskConfigModal } from "@/components/RiskConfigModal";
import { SpreadPayoffModal } from "@/components/SpreadPayoffModal";
import { TradeHistoryTable } from "@/components/TradeHistoryTable";
import { OrderBlotter } from "@/components/OrderBlotter";
import { ManualTradeModal } from "@/components/ManualTradeModal";
import { ScrollText, ShieldCheck, History, Receipt } from "lucide-react";

const useLive = (key, fn, interval = 8000) =>
  useQuery({ queryKey: [key], queryFn: fn, refetchInterval: interval });

function App() {
  const qc = useQueryClient();
  const { data: account } = useLive("account", getAccount);
  const { data: positions } = useLive("positions", getPositions);
  const { data: trades } = useLive("trades", getTrades, 12000);
  const { data: decisions } = useLive("decisions", getDecisions, 6000);
  const { data: pnl } = useLive("pnl", getPnl, 12000);
  const { data: orders } = useLive("orders", getOrders, 6000);
  const { data: status } = useLive("status", getStatus, 6000);
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const { data: llm } = useQuery({ queryKey: ["models"], queryFn: getModels });

  const [riskOpen, setRiskOpen] = useState(false);
  const [payoff, setPayoff] = useState(null);
  const [closingId, setClosingId] = useState(null);
  const [manualTradeOpen, setManualTradeOpen] = useState(false);
  const [manualTradeData, setManualTradeData] = useState(null);

  const handleOpenManualTrade = (proposalOrDecision = null) => {
    setManualTradeData(proposalOrDecision);
    setManualTradeOpen(true);
  };

  const refetchAll = () =>
    ["account", "positions", "trades", "decisions", "pnl", "status", "orders"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] })
    );

  const cycleMut = useMutation({
    mutationFn: runCycle,
    onSuccess: (r) => {
      const approved = (r.decisions || []).filter((d) => d.outcome === "approved").length;
      const rejected = (r.decisions || []).filter((d) => d.outcome === "rejected").length;
      if (r.status === "market_closed") toast.info("Market closed — forced demo cycle ran");
      toast.success(`Cycle ${r.cycle_id}: ${approved} approved · ${rejected} rejected · ${r.exits?.length || 0} exits`);
      refetchAll();
    },
    onError: () => toast.error("Cycle failed"),
  });

  const pauseMut = useMutation({
    mutationFn: pauseAgent,
    onSuccess: (r) => { toast[r.paused ? "warning" : "success"](r.paused ? "Agent paused" : "Agent resumed"); refetchAll(); },
  });

  const closeMut = useMutation({
    mutationFn: closePosition,
    onMutate: (id) => setClosingId(id),
    onSuccess: (r) => { toast.success(`Closed · realized ${r.realized_pnl >= 0 ? "+" : ""}$${Math.round(r.realized_pnl)}`); refetchAll(); },
    onSettled: () => setClosingId(null),
  });

  const saveConfig = async (draft) => {
    await updateConfig(draft);
    qc.invalidateQueries({ queryKey: ["config"] });
    toast.success("Risk parameters applied");
  };

  const gateDecisions = (decisions || []).filter((d) => d.gate_checks?.length > 0);

  return (
    <div data-testid="trading-terminal-root" className="min-h-screen grain">
      <Toaster theme="dark" position="top-right" toastOptions={{ style: { background: "#0c111a", border: "1px solid rgba(255,255,255,0.1)", color: "#e2e8f0", fontFamily: "JetBrains Mono", fontSize: 12 } }} />

      <HeaderTerminal
        account={account} status={status} agent={status?.agent} llm={llm}
        onRunCycle={() => cycleMut.mutate()} onPause={(p) => pauseMut.mutate(p)}
        onOpenRisk={() => setRiskOpen(true)} onOpenManualTrade={() => handleOpenManualTrade(null)}
        cycling={cycleMut.isPending} />

      <main className="mx-auto max-w-[1600px] px-4 sm:px-6 py-5 space-y-5 relative z-10">
        <MetricsRibbon account={account} />

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">
          <div className="xl:col-span-8 flex flex-col gap-5 min-w-0">
            <EquityChart pnl={pnl} initialEquity={account?.initial_equity || 100000} />
            <PositionsTable positions={positions} onClose={(id) => closeMut.mutate(id)}
              onPayoff={(p) => setPayoff(p)} closingId={closingId} />
          </div>
          <div className="xl:col-span-4 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-1 gap-5">
            <AgentReasoningPanel
              decisions={(decisions || []).slice(0, 30)}
              showGate={false}
              title="Live Decision Engine"
              onTradeOpportunity={handleOpenManualTrade}
            />
            <AskAgentChat />
          </div>
        </div>

        <div className="term-card overflow-hidden">
          <Tabs defaultValue="decisions" className="w-full">
            <TabsList className="w-full justify-start bg-[#0c111a] border-b border-[var(--border)] rounded-none px-3 h-auto py-0 relative z-10">
              {[
                { v: "decisions", i: ScrollText, l: "Decision Log" },
                { v: "gate", i: ShieldCheck, l: "Risk Gate Audit" },
                { v: "history", i: History, l: "Trade History" },
                { v: "orders", i: Receipt, l: "Order Blotter" },
              ].map((t) => (
                <TabsTrigger key={t.v} value={t.v} data-testid={`tab-${t.v}`}
                  className="data-[state=active]:bg-transparent data-[state=active]:text-[#00F0B5] data-[state=active]:border-b-2 data-[state=active]:border-[#00F0B5] rounded-none text-slate-500 font-mono text-xs uppercase tracking-wider py-3 px-3 -mb-px">
                  <t.i size={13} className="mr-1.5" /> {t.l}
                </TabsTrigger>
              ))}
            </TabsList>
            <TabsContent value="decisions" className="p-3 mt-0">
              <AgentReasoningPanel
                decisions={decisions}
                showGate
                height="480px"
                title="Full Reasoning + Gate Audit Trail"
                onTradeOpportunity={handleOpenManualTrade}
              />
            </TabsContent>
            <TabsContent value="gate" className="p-3 mt-0">
              <AgentReasoningPanel
                decisions={gateDecisions}
                showGate
                height="480px"
                title="Deterministic Risk-Gate Telemetry"
                onTradeOpportunity={handleOpenManualTrade}
              />
            </TabsContent>
            <TabsContent value="history" className="mt-0">
              <TradeHistoryTable trades={trades} />
            </TabsContent>
            <TabsContent value="orders" className="mt-0">
              <OrderBlotter orders={orders} />
            </TabsContent>
          </Tabs>
        </div>

        <footer className="text-center text-[10px] font-mono text-slate-700 py-4">
          PETRA · OPTIONS ALPHA AGENT · LLM SIGNAL ({llm?.provider === "Featherless AI" ? `FEATHERLESS · ${(llm?.active_model || "").split("/").pop().toUpperCase() || "QWEN"}` : "CLAUDE SONNET 4.6"}) → DETERMINISTIC STRIKE/SIZE ENGINE → HARD RISK GATE → ALPACA MLEG · PAPER {(account?.mode || "LIVE").toUpperCase()}
        </footer>
      </main>

      <RiskConfigModal open={riskOpen} onOpenChange={setRiskOpen} config={config} onSave={saveConfig} />
      <SpreadPayoffModal position={payoff} open={!!payoff} onOpenChange={(o) => !o && setPayoff(null)} />
      <ManualTradeModal
        open={manualTradeOpen}
        onClose={() => setManualTradeOpen(false)}
        initialData={manualTradeData}
        onSuccess={refetchAll}
      />
    </div>
  );
}

export default App;

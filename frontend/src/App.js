import { useState, useMemo } from "react";
import "@/App.css";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  getAccount, getPositions, getTrades, getDecisions, getPnl, getStatus,
  getConfig, updateConfig, runCycle, pauseAgent, closePosition, getOrders,
  getModels, getMarketLive, getAuthStatus, setOperatorToken,
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
import { MarketTickerStrip } from "@/components/MarketTickerStrip";
import { TradeWindow } from "@/components/TradeWindow";
import { BotActivityFeed } from "@/components/BotActivityFeed";
import { ScrollText, ShieldCheck, History, Receipt, LayoutGrid } from "lucide-react";

const useLive = (key, fn, interval = 8000, enabled = true) =>
  useQuery({ queryKey: [key], queryFn: fn, refetchInterval: interval, enabled });

function App() {
  const qc = useQueryClient();
  const { data: auth, refetch: refetchAuth, isLoading: authLoading } = useQuery({
    queryKey: ["auth-status"],
    queryFn: getAuthStatus,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const apiEnabled = auth?.authenticated === true;

  const { data: account } = useLive("account", getAccount, 8000, apiEnabled);
  const { data: positions } = useLive("positions", getPositions, 8000, apiEnabled);
  const { data: trades } = useLive("trades", getTrades, 12000, apiEnabled);
  const { data: decisions } = useLive("decisions", getDecisions, 6000, apiEnabled);
  const { data: pnl } = useLive("pnl", getPnl, 12000, apiEnabled);
  const { data: orders } = useLive("orders", getOrders, 6000, apiEnabled);
  const { data: status } = useLive("status", getStatus, 6000, apiEnabled);
  const { data: liveMarket } = useLive("market-live", getMarketLive, 10000, apiEnabled);
  const { data: config } = useQuery({ queryKey: ["config"], queryFn: getConfig, enabled: apiEnabled });
  const { data: llm } = useQuery({ queryKey: ["models"], queryFn: getModels, enabled: apiEnabled });

  const [loginToken, setLoginToken] = useState("");
  const [loginError, setLoginError] = useState("");

  const [riskOpen, setRiskOpen] = useState(false);
  const [payoff, setPayoff] = useState(null);
  const [closingId, setClosingId] = useState(null);

  // Manual Trade Modal state (upstream feature)
  const [manualTradeOpen, setManualTradeOpen] = useState(false);
  const [manualTradeData, setManualTradeData] = useState(null);

  // Client-side cache to guarantee agent cycle outcomes stay permanently in view
  const [localDecisions, setLocalDecisions] = useState(() => {
    try {
      const saved = localStorage.getItem("petra_decisions_v2");
      return saved ? JSON.parse(saved) : [];
    } catch {
      return [];
    }
  });

  const mergedDecisions = useMemo(() => {
    const map = new Map();
    (decisions || []).forEach((d) => {
      const key = d.id || `${d.cycle_id}-${d.underlying}-${d.created_at}`;
      map.set(key, d);
    });
    (localDecisions || []).forEach((d) => {
      const key = d.id || `${d.cycle_id}-${d.underlying}-${d.created_at}`;
      if (!map.has(key)) map.set(key, d);
    });
    return Array.from(map.values()).sort(
      (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)
    );
  }, [decisions, localDecisions]);

  const handleOpenManualTrade = (proposalOrDecision = null) => {
    setManualTradeData(proposalOrDecision);
    setManualTradeOpen(true);
  };

  // Trade Window state (our new feature)
  const [tradeOpen, setTradeOpen] = useState(false);
  const [tradeSymbol, setTradeSymbol] = useState(null);

  const openTrade = (symbolOrEvent) => {
    if (symbolOrEvent && symbolOrEvent.symbol) {
      setTradeSymbol(symbolOrEvent.symbol);
    } else {
      setTradeSymbol(null);
    }
    setTradeOpen(true);
  };

  const refetchAll = () =>
    ["account", "positions", "trades", "decisions", "pnl", "status", "orders", "market-live"].forEach((k) =>
      qc.invalidateQueries({ queryKey: [k] })
    );

  const cycleMut = useMutation({
    mutationFn: runCycle,
    onSuccess: (r) => {
      const approved = (r.decisions || []).filter((d) => d.outcome === "approved").length;
      const rejected = (r.decisions || []).filter((d) => d.outcome === "rejected").length;
      if (r.status === "market_closed") toast.info("Market closed — forced demo cycle ran");
      toast.success(`Cycle ${r.cycle_id}: ${approved} approved · ${rejected} rejected · ${r.exits?.length || 0} exits`);

      if (r.decisions && r.decisions.length > 0) {
        setLocalDecisions((prev) => {
          const map = new Map();
          r.decisions.forEach((d) => {
            const key = d.id || `${d.cycle_id}-${d.underlying}-${d.created_at}`;
            map.set(key, d);
          });
          prev.forEach((d) => {
            const key = d.id || `${d.cycle_id}-${d.underlying}-${d.created_at}`;
            if (!map.has(key)) map.set(key, d);
          });
          const updated = Array.from(map.values()).slice(0, 100);
          try {
            localStorage.setItem("petra_decisions_v2", JSON.stringify(updated));
          } catch (e) {
            console.warn("Storage write failed", e);
          }
          return updated;
        });
      }

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

  const gateDecisions = (mergedDecisions || []).filter((d) => d.gate_checks?.length > 0);

  const handleUnlock = async (e) => {
    e.preventDefault();
    const token = loginToken.trim();
    if (!token) {
      setLoginError("Enter the operator token configured for this deployment.");
      return;
    }
    setOperatorToken(token);
    const result = await refetchAuth();
    if (!result.data?.authenticated) {
      setOperatorToken("");
      setLoginError("That operator token was not accepted.");
      return;
    }
    setLoginError("");
    setLoginToken("");
  };

  if (authLoading || !auth) {
    return (
      <div className="min-h-screen grain flex items-center justify-center bg-[#070b12] text-slate-300 font-mono">
        PETRA · SECURE TERMINAL STARTING…
      </div>
    );
  }

  if (!auth.authenticated) {
    return (
      <div className="min-h-screen grain flex items-center justify-center bg-[#070b12] px-5">
        <form onSubmit={handleUnlock} className="term-card w-full max-w-md p-6 space-y-4">
          <div>
            <div className="text-[#00F0B5] font-mono text-xs tracking-[0.25em] uppercase">Petra Secure Terminal</div>
            <h1 className="text-xl font-semibold text-slate-100 mt-2">Operator authentication required</h1>
            <p className="text-sm text-slate-500 mt-2">
              Enter the private operator token configured in the deployment environment.
            </p>
          </div>
          <input
            type="password"
            value={loginToken}
            onChange={(e) => setLoginToken(e.target.value)}
            autoComplete="current-password"
            placeholder="PETRA_OPERATOR_TOKEN"
            className="w-full rounded border border-slate-700 bg-[#0c111a] px-3 py-2.5 text-slate-100 outline-none focus:border-[#00F0B5]"
          />
          {loginError && <div className="text-sm text-rose-400">{loginError}</div>}
          <button
            type="submit"
            className="w-full rounded bg-[#00F0B5] px-4 py-2.5 font-semibold text-[#07110e]"
          >
            Unlock Petra
          </button>
        </form>
      </div>
    );
  }

  return (
    <div data-testid="trading-terminal-root" className="min-h-screen grain">
      <Toaster theme="dark" position="top-right" toastOptions={{ style: { background: "#0c111a", border: "1px solid rgba(255,255,255,0.1)", color: "#e2e8f0", fontFamily: "JetBrains Mono", fontSize: 12 } }} />

      {/* ── Header ── */}
      <HeaderTerminal
        account={account} status={status} agent={status?.agent} llm={llm}
        onRunCycle={() => cycleMut.mutate()} onPause={(p) => pauseMut.mutate(p)}
        onOpenRisk={() => setRiskOpen(true)}
        onOpenManualTrade={() => handleOpenManualTrade(null)}
        onOpenTrade={() => openTrade(null)}
        cycling={cycleMut.isPending} />

      {/* ── Live Market Ticker Strip ── */}
      <MarketTickerStrip liveMarket={liveMarket} onSymbolClick={openTrade} />

      <main className="mx-auto max-w-[1600px] px-4 sm:px-6 py-5 space-y-5 relative z-10">
        <MetricsRibbon account={account} />

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">
          <div className="xl:col-span-8 flex flex-col gap-5 min-w-0">
            <EquityChart pnl={pnl} initialEquity={account?.initial_equity || 100000} />
            <PositionsTable positions={positions} trades={trades} onClose={(id) => closeMut.mutate(id)}
              onPayoff={(p) => setPayoff(p)} closingId={closingId} />
          </div>
          <div className="xl:col-span-4 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-1 gap-5">
            <AgentReasoningPanel decisions={(decisions || []).slice(0, 30)} showGate={false} title="Live Decision Engine" onTradeOpportunity={handleOpenManualTrade} />
            {/* Bot Activity Feed */}
            <BotActivityFeed decisions={decisions} />
            <AskAgentChat />
          </div>
        </div>

        <div className="term-card overflow-hidden">
          <Tabs defaultValue="trading" className="w-full">
            <TabsList className="w-full justify-start bg-[#0c111a] border-b border-[var(--border)] rounded-none px-3 h-auto py-0 relative z-10 flex-wrap">
              {[
                { v: "trading", i: LayoutGrid, l: "Trading Desk" },
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
            <TabsContent value="trading" className="p-3 mt-0">
              <TradingPlatform account={account} onOrderExecuted={refetchAll} />
            </TabsContent>
            <TabsContent value="decisions" className="p-3 mt-0">
              <AgentReasoningPanel decisions={decisions} showGate height="480px" title="Full Reasoning + Gate Audit Trail" onTradeOpportunity={handleOpenManualTrade} />
            </TabsContent>
            <TabsContent value="gate" className="p-3 mt-0">
              <AgentReasoningPanel decisions={gateDecisions} showGate height="480px" title="Deterministic Risk-Gate Telemetry" onTradeOpportunity={handleOpenManualTrade} />
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
          PETRA · OPTIONS ALPHA AGENT · LLM SIGNAL ({llm?.provider === "Featherless AI" ? `FEATHERLESS · ${llm.active_model.split("/").pop().toUpperCase()}` : "DETERMINISTIC / RULE-BASED"}) → DETERMINISTIC STRIKE/SIZE ENGINE → HARD RISK GATE → ALPACA MLEG · PAPER {account?.mode?.toUpperCase()}
        </footer>
      </main>

      {/* ── Modals ── */}
      <RiskConfigModal open={riskOpen} onOpenChange={setRiskOpen} config={config} onSave={saveConfig} />
      <SpreadPayoffModal position={payoff} open={!!payoff} onOpenChange={(o) => !o && setPayoff(null)} />

      <ManualTradeModal
        open={manualTradeOpen}
        onClose={() => setManualTradeOpen(false)}
        initialData={manualTradeData}
        onSuccess={refetchAll}
      />

      {/* ── Trade Window (slide-over) ── */}
      <TradeWindow
        open={tradeOpen}
        onClose={() => setTradeOpen(false)}
        initialSymbol={tradeSymbol}
        liveMarket={liveMarket}
      />
    </div>
  );
}

export default App;

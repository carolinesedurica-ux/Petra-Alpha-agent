import { useMemo, useState } from "react";
import {
  ShieldCheck, Wallet, Layers3, Receipt, RefreshCw, Zap, Gauge, Crosshair,
  Activity, Play, Square, TrendingUp, TrendingDown, History,
} from "lucide-react";

const Tile = ({ icon: Icon, label, value, sub }) => (
  <div className="term-card p-5">
    <div className="flex items-center gap-2 text-slate-500 mb-2">
      <Icon size={14} />
      <span className="text-[10px] font-mono uppercase tracking-[0.18em]">{label}</span>
    </div>
    <div className="text-2xl font-mono font-bold text-slate-100">{value}</div>
    {sub && <div className="mt-1 text-xs font-mono text-slate-500">{sub}</div>}
  </div>
);

const PilotRow = ({ label, value, tone = "text-slate-200" }) => (
  <div className="flex justify-between gap-4">
    <span className="text-slate-500">{label}</span>
    <span className={tone}>{value}</span>
  </div>
);

const money = (value) => {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(2)}`;
};

export const CTraderReadOnlyDashboard = ({ status, snapshot, loading, error, onRefresh }) => {
  const [shadowCheckedAt, setShadowCheckedAt] = useState(null);
  const [side, setSide] = useState("BUY");
  const [entryInput, setEntryInput] = useState("6000");
  const [stopInput, setStopInput] = useState("5985");
  const [targetInput, setTargetInput] = useState("6030");
  const [usdPerPointInput, setUsdPerPointInput] = useState("0.10");
  const [simTrade, setSimTrade] = useState(null);
  const [simPrice, setSimPrice] = useState(null);
  const [simHistory, setSimHistory] = useState([]);
  const [simMessage, setSimMessage] = useState("");

  const balance = snapshot?.balance;
  const balanceText = Number.isFinite(balance)
    ? `$${Number(balance).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "—";

  const pilot = useMemo(() => {
    const authenticated = snapshot?.authenticated === true;
    const openPositions = Number(snapshot?.open_positions ?? 0);
    const pendingOrders = Number(snapshot?.pending_orders ?? 0);
    const dryRun = status?.dry_run === true;
    const pilotArmed = status?.pilot_armed === true;
    const liveTrading = status?.allow_live_trading === true;
    const referenceMargin = 3.91;
    const marginCap = 5.0;

    const checks = [
      { name: "Demo account authenticated", pass: authenticated },
      { name: "Open positions below cap", pass: openPositions < 1 },
      { name: "No pending orders", pass: pendingOrders === 0 },
      { name: "US500 reference margin <= $5", pass: referenceMargin <= marginCap },
      { name: "Dry run remains enabled", pass: dryRun },
      { name: "Live execution remains disabled", pass: !pilotArmed && !liveTrading },
    ];

    const ready = checks.every((check) => check.pass);
    return { ready, checks, referenceMargin, marginCap };
  }, [snapshot, status]);

  const simulatedPnl = useMemo(() => {
    if (!simTrade || !Number.isFinite(simPrice)) return 0;
    const points = simTrade.side === "BUY"
      ? simPrice - simTrade.entry
      : simTrade.entry - simPrice;
    return points * simTrade.usdPerPoint;
  }, [simTrade, simPrice]);

  const stopHit = useMemo(() => {
    if (!simTrade || !Number.isFinite(simPrice)) return false;
    return simTrade.side === "BUY" ? simPrice <= simTrade.stop : simPrice >= simTrade.stop;
  }, [simTrade, simPrice]);

  const targetHit = useMemo(() => {
    if (!simTrade || !Number.isFinite(simPrice)) return false;
    return simTrade.side === "BUY" ? simPrice >= simTrade.target : simPrice <= simTrade.target;
  }, [simTrade, simPrice]);

  const startSimulation = () => {
    const entry = Number(entryInput);
    const stop = Number(stopInput);
    const target = Number(targetInput);
    const usdPerPoint = Number(usdPerPointInput);

    if (!pilot.ready) {
      setSimMessage("Pilot gates are not clear. Simulation start blocked.");
      return;
    }
    if (simTrade) {
      setSimMessage("Only one simulated position is allowed at a time.");
      return;
    }
    if (![entry, stop, target, usdPerPoint].every((v) => Number.isFinite(v) && v > 0)) {
      setSimMessage("Enter valid positive simulation values.");
      return;
    }
    if (side === "BUY" && !(stop < entry && target > entry)) {
      setSimMessage("BUY simulation requires stop below entry and target above entry.");
      return;
    }
    if (side === "SELL" && !(stop > entry && target < entry)) {
      setSimMessage("SELL simulation requires stop above entry and target below entry.");
      return;
    }

    const trade = {
      id: Date.now(),
      side,
      symbol: "US500",
      size: 0.10,
      entry,
      stop,
      target,
      usdPerPoint,
      openedAt: new Date(),
    };
    setSimTrade(trade);
    setSimPrice(entry);
    setSimMessage("Simulation running. No order was sent to Pepperstone.");
  };

  const closeSimulation = (reason = "MANUAL") => {
    if (!simTrade || !Number.isFinite(simPrice)) return;
    const closed = {
      ...simTrade,
      exit: simPrice,
      pnl: simulatedPnl,
      reason,
      closedAt: new Date(),
    };
    setSimHistory((prev) => [closed, ...prev].slice(0, 10));
    setSimTrade(null);
    setSimPrice(null);
    setSimMessage(`Simulation closed (${reason}). No broker order was involved.`);
  };

  const movePrice = (delta) => {
    if (!simTrade) return;
    const next = Number((simPrice + delta).toFixed(2));
    setSimPrice(next);
  };

  return (
    <div className="min-h-screen grain bg-[#070b12] text-slate-100">
      <header className="border-b border-[var(--border)] bg-[#06090e]/95 backdrop-blur-xl">
        <div className="mx-auto max-w-[1500px] px-4 sm:px-6 py-5 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-md flex items-center justify-center border border-[var(--border-accent)] bg-[#00F0B5]/10">
              <Zap size={19} color="#00F0B5" />
            </div>
            <div>
              <h1 className="font-display text-lg sm:text-xl font-bold">PETRA <span className="text-[#00F0B5]">// cTRADER DEMO</span></h1>
              <div className="text-[10px] font-mono uppercase tracking-[0.22em] text-slate-500 mt-1">PEPPERSTONE · READ ONLY · EXECUTION DISABLED</div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
            <span className="term-well px-3 py-2 text-[#00F0B5]">CTRADER · {status?.ctrader_environment?.toUpperCase() || "DEMO"}</span>
            <span className="term-well px-3 py-2 text-slate-300">API ID · {status?.expected_account_id || "—"}</span>
            <span className="term-well px-3 py-2 text-[#FFB800]">DRY RUN · ON</span>
            <span className="term-well px-3 py-2 text-[#FF3B69]">LIVE TRADING · OFF</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1500px] px-4 sm:px-6 py-6 space-y-5">
        <div className="term-card p-4 flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-xs font-mono uppercase tracking-[0.2em] text-[#00F0B5]">Pepperstone cTrader connection</div>
            <div className="mt-1 text-sm text-slate-400">
              {loading ? "Reading the authorized demo account…" : error ? "Snapshot unavailable" : snapshot?.status === "ok" ? "Demo account authenticated and readable." : "Waiting for broker snapshot."}
            </div>
          </div>
          <button onClick={onRefresh} disabled={loading}
            className="term-well px-4 py-2 text-xs font-mono text-slate-200 hover:text-white disabled:opacity-50 flex items-center gap-2">
            <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> REFRESH ACCOUNT
          </button>
        </div>

        {error && (
          <div className="term-card border border-[#FF3B69]/40 p-4 text-sm font-mono text-[#FF6B8A]">
            cTrader snapshot failed. Petra remains read-only and execution is disabled. {error?.response?.data?.error || error?.message || ""}
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <Tile icon={Wallet} label="Demo Balance" value={balanceText} sub={`Broker: ${snapshot?.broker_name || "Pepperstone"}`} />
          <Tile icon={Layers3} label="Open Positions" value={snapshot?.open_positions ?? "—"} sub="Read from cTrader reconcile" />
          <Tile icon={Receipt} label="Pending Orders" value={snapshot?.pending_orders ?? "—"} sub="Read from cTrader reconcile" />
          <Tile icon={ShieldCheck} label="Execution" value="DISABLED" sub="Dry-run on · pilot unarmed" />
        </div>

        <div className="term-card border border-[#00F0B5]/30 overflow-hidden">
          <div className="p-5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[#00F0B5]">
                <TrendingUp size={15} />
                <span className="text-xs font-mono uppercase tracking-[0.2em]">cTrader Demo Trade Simulator</span>
              </div>
              <div className="mt-2 text-sm text-slate-400">Model US500 trades against the authenticated demo-account safety state. Prices and P&amp;L below are simulated locally; nothing is submitted to Pepperstone.</div>
            </div>
            <span className="term-well px-3 py-2 font-mono text-xs text-[#FFB800]">SIMULATION · 0.10 SIZE</span>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-12 gap-0">
            <div className="p-5 xl:col-span-4 xl:border-r border-[var(--border)]">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500 mb-4">Trade setup</div>

              <div className="grid grid-cols-2 gap-2 mb-4">
                <button
                  onClick={() => !simTrade && setSide("BUY")}
                  className={`term-well py-2.5 font-mono text-xs flex items-center justify-center gap-2 ${side === "BUY" ? "text-[#00F0B5] border-[#00F0B5]/40" : "text-slate-500"}`}
                ><TrendingUp size={14} /> BUY</button>
                <button
                  onClick={() => !simTrade && setSide("SELL")}
                  className={`term-well py-2.5 font-mono text-xs flex items-center justify-center gap-2 ${side === "SELL" ? "text-[#FF6B8A] border-[#FF3B69]/40" : "text-slate-500"}`}
                ><TrendingDown size={14} /> SELL</button>
              </div>

              <div className="space-y-3 font-mono text-xs">
                <label className="block">
                  <span className="text-slate-500">Entry / synthetic US500 price</span>
                  <input disabled={!!simTrade} value={entryInput} onChange={(e) => setEntryInput(e.target.value)} type="number" step="0.1"
                    className="mt-1 w-full term-well bg-transparent px-3 py-2 text-slate-200 outline-none disabled:opacity-50" />
                </label>
                <label className="block">
                  <span className="text-slate-500">Protective stop</span>
                  <input disabled={!!simTrade} value={stopInput} onChange={(e) => setStopInput(e.target.value)} type="number" step="0.1"
                    className="mt-1 w-full term-well bg-transparent px-3 py-2 text-slate-200 outline-none disabled:opacity-50" />
                </label>
                <label className="block">
                  <span className="text-slate-500">Take-profit target</span>
                  <input disabled={!!simTrade} value={targetInput} onChange={(e) => setTargetInput(e.target.value)} type="number" step="0.1"
                    className="mt-1 w-full term-well bg-transparent px-3 py-2 text-slate-200 outline-none disabled:opacity-50" />
                </label>
                <label className="block">
                  <span className="text-slate-500">Simulation USD / point</span>
                  <input disabled={!!simTrade} value={usdPerPointInput} onChange={(e) => setUsdPerPointInput(e.target.value)} type="number" min="0.01" step="0.01"
                    className="mt-1 w-full term-well bg-transparent px-3 py-2 text-slate-200 outline-none disabled:opacity-50" />
                </label>
              </div>

              {!simTrade ? (
                <button onClick={startSimulation} disabled={!pilot.ready}
                  className="mt-5 w-full rounded border border-[#00F0B5]/40 bg-[#00F0B5]/10 px-4 py-3 text-xs font-mono text-[#00F0B5] hover:bg-[#00F0B5]/15 disabled:opacity-40 flex items-center justify-center gap-2">
                  <Play size={14} /> START SIMULATION
                </button>
              ) : (
                <button onClick={() => closeSimulation("MANUAL")}
                  className="mt-5 w-full rounded border border-[#FF3B69]/40 bg-[#FF3B69]/10 px-4 py-3 text-xs font-mono text-[#FF6B8A] hover:bg-[#FF3B69]/15 flex items-center justify-center gap-2">
                  <Square size={13} /> CLOSE SIMULATION
                </button>
              )}

              <div className="mt-3 text-[10px] leading-relaxed font-mono text-slate-600">The USD/point field is an explicit simulator assumption, not a broker contract specification or live quote.</div>
            </div>

            <div className="p-5 xl:col-span-4 xl:border-r border-[var(--border)]">
              <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-slate-500 mb-4">Running simulation</div>
              {simTrade ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 font-mono text-sm">
                    <div className="term-well p-3"><div className="text-[10px] text-slate-500">SIDE</div><div className={simTrade.side === "BUY" ? "text-[#00F0B5]" : "text-[#FF6B8A]"}>{simTrade.side}</div></div>
                    <div className="term-well p-3"><div className="text-[10px] text-slate-500">SIZE</div><div>0.10</div></div>
                    <div className="term-well p-3"><div className="text-[10px] text-slate-500">ENTRY</div><div>{simTrade.entry.toFixed(2)}</div></div>
                    <div className="term-well p-3"><div className="text-[10px] text-slate-500">SIM PRICE</div><div>{simPrice.toFixed(2)}</div></div>
                  </div>

                  <div className="term-well p-4">
                    <div className="text-[10px] font-mono text-slate-500 uppercase tracking-[0.18em]">Simulated P&amp;L</div>
                    <div className={`mt-1 text-3xl font-mono font-bold ${simulatedPnl >= 0 ? "text-[#00F0B5]" : "text-[#FF6B8A]"}`}>{money(simulatedPnl)}</div>
                    <div className="mt-2 text-xs font-mono text-slate-500">Stop {simTrade.stop.toFixed(2)} · Target {simTrade.target.toFixed(2)}</div>
                  </div>

                  <div className="grid grid-cols-4 gap-2">
                    {[-5, -1, 1, 5].map((delta) => (
                      <button key={delta} onClick={() => movePrice(delta)}
                        className="term-well py-2 text-xs font-mono text-slate-300 hover:text-white">
                        {delta > 0 ? "+" : ""}{delta} pt
                      </button>
                    ))}
                  </div>

                  {(stopHit || targetHit) && (
                    <div className={`rounded border p-3 text-xs font-mono ${targetHit ? "border-[#00F0B5]/40 text-[#00F0B5] bg-[#00F0B5]/5" : "border-[#FF3B69]/40 text-[#FF6B8A] bg-[#FF3B69]/5"}`}>
                      {targetHit ? "TARGET REACHED" : "STOP REACHED"} — close the simulation to record the outcome.
                    </div>
                  )}
                </div>
              ) : (
                <div className="h-full min-h-[260px] flex flex-col items-center justify-center text-center">
                  <Crosshair size={28} className="text-slate-700 mb-3" />
                  <div className="font-mono text-sm text-slate-400">No simulated position open</div>
                  <div className="mt-2 max-w-xs text-xs text-slate-600">Choose BUY or SELL, set an entry/stop/target, then start a simulation.</div>
                </div>
              )}
              {simMessage && <div className="mt-4 text-[11px] font-mono text-slate-500">{simMessage}</div>}
            </div>

            <div className="p-5 xl:col-span-4">
              <div className="flex items-center gap-2 text-slate-500 mb-4">
                <History size={14} />
                <span className="text-[10px] font-mono uppercase tracking-[0.18em]">Simulation history</span>
              </div>
              {simHistory.length === 0 ? (
                <div className="text-xs font-mono text-slate-600">No completed simulations yet.</div>
              ) : (
                <div className="space-y-2">
                  {simHistory.map((t) => (
                    <div key={t.id} className="term-well p-3 font-mono text-xs">
                      <div className="flex justify-between gap-3">
                        <span className={t.side === "BUY" ? "text-[#00F0B5]" : "text-[#FF6B8A]"}>{t.side} US500 · 0.10</span>
                        <span className={t.pnl >= 0 ? "text-[#00F0B5]" : "text-[#FF6B8A]"}>{money(t.pnl)}</span>
                      </div>
                      <div className="mt-1 text-slate-600">{t.entry.toFixed(2)} → {t.exit.toFixed(2)} · {t.reason}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="term-card border border-[#00F0B5]/30 overflow-hidden">
          <div className="p-5 border-b border-[var(--border)] flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[#00F0B5]">
                <Crosshair size={15} />
                <span className="text-xs font-mono uppercase tracking-[0.2em]">Demo Pilot // Shadow Layer</span>
              </div>
              <div className="mt-2 text-sm text-slate-400">US500 minimum-size pilot policy is evaluated against the live demo account snapshot. No broker order can be submitted from this layer.</div>
            </div>
            <span className={`term-well px-3 py-2 font-mono text-xs ${pilot.ready ? "text-[#00F0B5]" : "text-[#FF3B69]"}`}>
              {pilot.ready ? "READY TO SIMULATE" : "BLOCKED"}
            </span>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-0">
            <div className="p-5 xl:border-r border-[var(--border)]">
              <div className="flex items-center gap-2 text-slate-500 mb-4">
                <Gauge size={14} />
                <span className="text-[10px] font-mono uppercase tracking-[0.18em]">Pilot policy</span>
              </div>
              <div className="space-y-3 font-mono text-sm">
                <PilotRow label="Instrument" value="US500" />
                <PilotRow label="Minimum size" value="0.10" />
                <PilotRow label="Reference margin" value={`$${pilot.referenceMargin.toFixed(2)}`} tone="text-[#00F0B5]" />
                <PilotRow label="Margin cap" value={`$${pilot.marginCap.toFixed(2)}`} />
                <PilotRow label="Max open positions" value="1" />
                <PilotRow label="Pending orders allowed" value="0" />
                <PilotRow label="Daily loss stop" value="$1.00" />
              </div>
              <div className="mt-4 text-[10px] leading-relaxed font-mono text-slate-600">Reference margin is from Petra's validated cTrader Phase 2 probe. It is a pilot sizing reference, not a live quote.</div>
            </div>

            <div className="p-5 xl:border-r border-[var(--border)]">
              <div className="flex items-center gap-2 text-slate-500 mb-4">
                <ShieldCheck size={14} />
                <span className="text-[10px] font-mono uppercase tracking-[0.18em]">Preflight gates</span>
              </div>
              <div className="space-y-3 font-mono text-xs">
                {pilot.checks.map((check) => (
                  <div key={check.name} className="flex justify-between gap-4">
                    <span className="text-slate-500">{check.name}</span>
                    <span className={check.pass ? "text-[#00F0B5]" : "text-[#FF3B69]"}>{check.pass ? "PASS" : "BLOCK"}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="p-5">
              <div className="flex items-center gap-2 text-slate-500 mb-4">
                <Activity size={14} />
                <span className="text-[10px] font-mono uppercase tracking-[0.18em]">Shadow decision</span>
              </div>
              <div className="font-mono text-sm space-y-3">
                <PilotRow label="Execution mode" value="SIMULATION ONLY" tone="text-[#FFB800]" />
                <PilotRow label="Broker submission" value="DISABLED" tone="text-[#FF3B69]" />
                <PilotRow label="Current result" value={pilot.ready ? "ELIGIBLE / WAIT SIGNAL" : "NO TRADE"} tone={pilot.ready ? "text-[#00F0B5]" : "text-[#FF3B69]"} />
                <PilotRow label="Last shadow check" value={shadowCheckedAt ? shadowCheckedAt.toLocaleTimeString() : "NOT RUN"} />
              </div>
              <button
                onClick={() => setShadowCheckedAt(new Date())}
                disabled={!snapshot?.authenticated}
                className="mt-5 w-full term-well px-4 py-2.5 text-xs font-mono text-slate-200 hover:text-white disabled:opacity-40"
              >
                RUN SHADOW CHECK
              </button>
              <div className="mt-3 text-[10px] leading-relaxed font-mono text-slate-600">This check only evaluates account state and risk policy. It does not generate or transmit an order.</div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div className="term-card p-5">
            <div className="text-xs font-mono uppercase tracking-[0.18em] text-slate-500 mb-4">Connection details</div>
            <div className="space-y-3 font-mono text-sm">
              <div className="flex justify-between gap-4"><span className="text-slate-500">Provider</span><span className="text-slate-200">cTrader</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Environment</span><span className="text-[#00F0B5]">DEMO</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">API account ID</span><span className="text-slate-200">{snapshot?.account_id || status?.expected_account_id || "—"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Authenticated</span><span className="text-[#00F0B5]">{snapshot?.authenticated ? "YES" : "—"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Execution enabled</span><span className="text-[#FF3B69]">NO</span></div>
            </div>
          </div>

          <div className="term-card p-5">
            <div className="text-xs font-mono uppercase tracking-[0.18em] text-slate-500 mb-4">Safety gate</div>
            <div className="space-y-3 font-mono text-sm">
              <div className="flex justify-between gap-4"><span className="text-slate-500">Runtime ready</span><span className="text-[#00F0B5]">{status?.ready_for_read_only ? "YES" : "NO"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Dry run</span><span className="text-[#00F0B5]">{status?.dry_run ? "ON" : "OFF"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Pilot armed</span><span className="text-slate-200">{status?.pilot_armed ? "YES" : "NO"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Allow live trading</span><span className="text-[#FF3B69]">{status?.allow_live_trading ? "YES" : "NO"}</span></div>
              <div className="flex justify-between gap-4"><span className="text-slate-500">Mode</span><span className="text-[#FFB800]">READ ONLY + DEMO SIMULATOR</span></div>
            </div>
          </div>
        </div>

        <div className="text-center text-[10px] font-mono uppercase tracking-[0.18em] text-slate-700 py-5">
          PETRA · PEPPERSTONE cTRADER DEMO · LOCAL TRADE SIMULATION · NO ORDER SUBMISSION
        </div>
      </main>
    </div>
  );
};

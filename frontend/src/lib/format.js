export const fmtUsd = (n, dp = 2) =>
  (n < 0 ? "-$" : "$") +
  Math.abs(Number(n) || 0).toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });

export const fmtNum = (n, dp = 2) =>
  Number(n || 0).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });

export const fmtPct = (n, dp = 2) => `${n >= 0 ? "+" : ""}${fmtNum(n, dp)}%`;

export const fmtSignedUsd = (n) => `${n >= 0 ? "+" : "-"}$${fmtNum(Math.abs(n), 0)}`;

export const strategyMeta = {
  put_credit_spread: { label: "PUT CREDIT", color: "#00F0B5", bg: "rgba(0,240,181,0.12)", bias: "BULLISH" },
  call_credit_spread: { label: "CALL CREDIT", color: "#FF3B69", bg: "rgba(255,59,105,0.12)", bias: "BEARISH" },
  iron_condor: { label: "IRON CONDOR", color: "#00D4FF", bg: "rgba(0,212,255,0.12)", bias: "RANGE" },
};

export const timeAgo = (iso) => {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};

export const legLabel = (leg) =>
  `${leg.side === "sell" ? "-" : "+"}${leg.strike}${leg.option_type === "call" ? "C" : "P"}`;

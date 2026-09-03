import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";  // empty → same-origin (Vercel)
export const API = `${BACKEND_URL}/api`;

const http = axios.create({ baseURL: API });

export const getAccount = () => http.get("/account").then((r) => r.data);
export const getPositions = () => http.get("/positions").then((r) => r.data);
export const getTrades = () => http.get("/trades").then((r) => r.data);
export const getDecisions = () => http.get("/decisions").then((r) => r.data);
export const getPnl = () => http.get("/pnl").then((r) => r.data);
export const getOrders = () => http.get("/orders").then((r) => r.data);
export const getModels = () => http.get("/models").then((r) => r.data);
export const getMarket = () => http.get("/market").then((r) => r.data);
export const getStatus = () => http.get("/status").then((r) => r.data);
export const getConfig = () => http.get("/config").then((r) => r.data);
export const updateConfig = (body) => http.put("/config", body).then((r) => r.data);
export const runCycle = () => http.post("/agent/run-cycle", { force: true }).then((r) => r.data);
export const pauseAgent = (paused) => http.post("/agent/pause", { paused }).then((r) => r.data);
export const closePosition = (id) => http.post(`/positions/${id}/close`).then((r) => r.data);

export async function streamChat(message, sessionId, onChunk) {
  const res = await fetch(`${API}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
}

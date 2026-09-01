"""Claude Sonnet 4.6 signal layer + 'Ask the Agent' chat. Strict JSON, safe fallback."""
import os
import json
import re

from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone

KEY = os.environ.get("EMERGENT_LLM_KEY")
MODEL = ("anthropic", "claude-sonnet-4-6")

SIGNAL_SYSTEM = (
    "You are the SIGNAL layer of an autonomous options-trading agent that trades "
    "defined-risk credit spreads. You ONLY reason about market regime and direction. "
    "You do NOT pick strikes, sizes, or place orders — a deterministic engine does that. "
    "Given a market snapshot, respond with ONE JSON object and nothing else:\n"
    '{"regime": "trending_up|trending_down|range_bound|high_volatility", '
    '"direction": "bullish|bearish|neutral", "confidence": 0.0-1.0, '
    '"chosen_strategy": "put_credit_spread|call_credit_spread|iron_condor", '
    '"rationale": "<=240 chars, cite the price action / IV you used"}\n'
    "Rules: uptrend -> put_credit_spread; downtrend -> call_credit_spread; "
    "range_bound or high_volatility with no clear direction -> iron_condor. "
    "Be disciplined: if signal is weak, lower confidence."
)


async def _complete(system, prompt, session_id):
    chat = LlmChat(api_key=KEY, session_id=session_id, system_message=system).with_model(*MODEL)
    text = ""
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            text += ev.content
        elif isinstance(ev, StreamDone):
            break
    return text


def _fallback_verdict(snap):
    chg = snap["change_pct"]
    if chg > 0.4:
        return {"regime": "trending_up", "direction": "bullish", "confidence": 0.55,
                "chosen_strategy": "put_credit_spread",
                "rationale": f"Fallback: {snap['symbol']} +{chg:.2f}% momentum, sell puts below.",
                "source": "fallback"}
    if chg < -0.4:
        return {"regime": "trending_down", "direction": "bearish", "confidence": 0.55,
                "chosen_strategy": "call_credit_spread",
                "rationale": f"Fallback: {snap['symbol']} {chg:.2f}% weakness, sell calls above.",
                "source": "fallback"}
    return {"regime": "range_bound", "direction": "neutral", "confidence": 0.5,
            "chosen_strategy": "iron_condor",
            "rationale": f"Fallback: {snap['symbol']} flat ({chg:.2f}%), collect premium both sides.",
            "source": "fallback"}


def _validate(v):
    strat = {"put_credit_spread", "call_credit_spread", "iron_condor"}
    if not isinstance(v, dict):
        return None
    if v.get("chosen_strategy") not in strat:
        return None
    try:
        conf = float(v.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    return {
        "regime": str(v.get("regime", "unknown"))[:40],
        "direction": str(v.get("direction", "neutral"))[:20],
        "confidence": max(0.0, min(1.0, conf)),
        "chosen_strategy": v["chosen_strategy"],
        "rationale": str(v.get("rationale", ""))[:280],
        "source": "llm",
    }


async def get_verdict(snap, cycle_id):
    prompt = (
        f"Market snapshot:\n"
        f"- Underlying: {snap['symbol']}\n"
        f"- Price: {snap['price']} (prev {snap['prev_price']}, {snap['change_pct']:+.2f}% intraday)\n"
        f"- Implied volatility: {snap['iv']:.0%}\n"
        f"- 5-step trend bias: {snap['trend']:+.2f}%/day\n"
        f"- News/sentiment: {snap['sentiment']}\n"
        "Return the JSON verdict."
    )
    try:
        raw = await _complete(SIGNAL_SYSTEM, prompt, f"signal-{cycle_id}-{snap['symbol']}")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else None
        v = _validate(parsed)
        if v:
            return v
    except Exception:
        pass
    return _fallback_verdict(snap)


CHAT_SYSTEM = (
    "You are Petra, an autonomous defined-risk options trader on an Alpaca "
    "paper account. You trade credit spreads (put/call credit spreads, iron condors) gated "
    "by a deterministic risk engine (max 2% risk/trade, max 5 concurrent, min 30% credit-to-width, "
    "delta ~0.20 shorts, 2-7 DTE, TP at 50% of credit, stop at 2x). Answer the operator's "
    "questions about your reasoning, positions, and risk clearly and concisely. Use the provided "
    "portfolio context. Be candid about risk. Keep answers tight."
)


async def chat_stream(question, context, session_id):
    prompt = f"PORTFOLIO CONTEXT (JSON):\n{json.dumps(context, default=str)}\n\nOPERATOR: {question}"
    chat = LlmChat(api_key=KEY, session_id=session_id, system_message=CHAT_SYSTEM).with_model(*MODEL)
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            yield ev.content
        elif isinstance(ev, StreamDone):
            break

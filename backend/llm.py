"""Multi-provider LLM signal layer & 'Ask the Agent' chat:
- Featherless AI (OpenAI-compatible serverless inference for open-weights: Qwen 3.6, DeepSeek R1, Mistral Large, etc.)
- Emergent LLM (Claude Sonnet fallback)
- Deterministic rule-based fallback
Strict JSON output, fail-closed safety.
"""
import os
import json
import re
import logging
from typing import Optional, Dict, Any, AsyncGenerator

logger = logging.getLogger("options_alpha.llm")

# OpenAI client for Featherless AI
try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None

# Emergent LLM client for Claude
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage, TextDelta, StreamDone
except ImportError:
    LlmChat = None
    UserMessage = None
    TextDelta = None
    StreamDone = None

FEATHERLESS_KEY = os.environ.get("FEATHERLESS_API_KEY")
FEATHERLESS_BASE = os.environ.get("FEATHERLESS_BASE_URL", "https://api.featherless.ai/v1")
FEATHERLESS_MODEL = os.environ.get("FEATHERLESS_MODEL", "Qwen/Qwen3.6-35B-A3B")

EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
EMERGENT_MODEL = ("anthropic", "claude-sonnet-4-6")

# Active provider detection
featherless_client: Optional[Any] = None
if FEATHERLESS_KEY and AsyncOpenAI:
    try:
        featherless_client = AsyncOpenAI(
            base_url=FEATHERLESS_BASE,
            api_key=FEATHERLESS_KEY,
            timeout=45.0
        )
        logger.info(f"Featherless AI initialized with model: {FEATHERLESS_MODEL}")
    except Exception as e:
        logger.error(f"Failed to initialize Featherless AI client: {e}")

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


async def _complete_featherless(system: str, prompt: str) -> str:
    """Call Featherless API using AsyncOpenAI, returning combined content + reasoning."""
    if not featherless_client:
        raise RuntimeError("Featherless client not initialized")
    
    response = await featherless_client.chat.completions.create(
        model=FEATHERLESS_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
        max_tokens=800
    )
    msg = response.choices[0].message
    content = msg.content or ""
    reasoning = getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None) or ""
    
    # If JSON is in content, return content; otherwise check reasoning
    if content.strip():
        return content
    return reasoning or ""


async def _complete_emergent(system: str, prompt: str, session_id: str) -> str:
    """Call Emergent Claude chat."""
    if not LlmChat or not EMERGENT_KEY:
        raise RuntimeError("Emergent LLM key not configured")
    chat = LlmChat(api_key=EMERGENT_KEY, session_id=session_id, system_message=system).with_model(*EMERGENT_MODEL)
    text = ""
    async for ev in chat.stream_message(UserMessage(text=prompt)):
        if isinstance(ev, TextDelta):
            text += ev.content
        elif isinstance(ev, StreamDone):
            break
    return text


def _fallback_verdict(snap: dict) -> dict:
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


def _validate(v: Any, provider: str = "llm") -> Optional[dict]:
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
        "source": provider,
    }


async def get_verdict(snap: dict, cycle_id: str) -> dict:
    prompt = (
        f"Market snapshot:\n"
        f"- Underlying: {snap['symbol']}\n"
        f"- Price: {snap['price']} (prev {snap['prev_price']}, {snap['change_pct']:+.2f}% intraday)\n"
        f"- Implied volatility: {snap['iv']:.0%}\n"
        f"- {snap.get('trend_label', '5-step trend bias')}: {snap['trend']:+.2f}%\n"
        f"- News/sentiment: {snap['sentiment']}\n"
        + (f"- Target expiry: {snap['expiry']}\n" if snap.get("expiry") else "")
        + "Return the JSON verdict."
    )
    
    # 1. Primary: Featherless AI
    if featherless_client:
        try:
            raw = await _complete_featherless(SIGNAL_SYSTEM, prompt)
            m = re.search(r"\{[\s\S]*?\}", raw)
            if m:
                parsed = json.loads(m.group(0))
                v = _validate(parsed, provider=f"featherless:{FEATHERLESS_MODEL.split('/')[-1]}")
                if v:
                    return v
        except Exception as ex:
            logger.warning(f"Featherless signal query failed for {snap['symbol']}: {ex}")

    # 2. Secondary: Emergent Claude Sonnet
    if EMERGENT_KEY and LlmChat:
        try:
            raw = await _complete_emergent(SIGNAL_SYSTEM, prompt, f"signal-{cycle_id}-{snap['symbol']}")
            m = re.search(r"\{[\s\S]*?\}", raw)
            if m:
                parsed = json.loads(m.group(0))
                v = _validate(parsed, provider="claude-sonnet-4-6")
                if v:
                    return v
        except Exception as ex:
            logger.warning(f"Emergent signal query failed for {snap['symbol']}: {ex}")

    # 3. Fallback: Deterministic math
    return _fallback_verdict(snap)


CHAT_SYSTEM = (
    "You are Petra, an autonomous defined-risk options trader on an Alpaca "
    "paper account. You trade credit spreads (put/call credit spreads, iron condors) gated "
    "by a deterministic risk engine (max 2% risk/trade, max 5 concurrent, min 30% credit-to-width, "
    "delta ~0.20 shorts, 2-7 DTE, TP at 50% of credit, stop at 2x). Answer the operator's "
    "questions about your reasoning, positions, and risk clearly and concisely. Use the provided "
    "portfolio context. Be candid about risk. Keep answers tight."
)


async def chat_stream(question: str, context: dict, session_id: str) -> AsyncGenerator[str, None]:
    prompt = f"PORTFOLIO CONTEXT (JSON):\n{json.dumps(context, default=str)}\n\nOPERATOR: {question}"
    
    # Featherless stream
    if featherless_client:
        try:
            stream = await featherless_client.chat.completions.create(
                model=FEATHERLESS_MODEL,
                messages=[
                    {"role": "system", "content": CHAT_SYSTEM},
                    {"role": "user", "content": prompt}
                ],
                stream=True,
                max_tokens=600,
                temperature=0.3
            )
            has_tokens = False
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = delta.content or getattr(delta, "reasoning", "") or getattr(delta, "reasoning_content", "") or ""
                if content:
                    has_tokens = True
                    yield content
            if has_tokens:
                return
        except Exception as ex:
            logger.error(f"Featherless chat stream error: {ex}")

    # Emergent Claude stream
    if EMERGENT_KEY and LlmChat:
        try:
            chat = LlmChat(api_key=EMERGENT_KEY, session_id=session_id, system_message=CHAT_SYSTEM).with_model(*EMERGENT_MODEL)
            async for ev in chat.stream_message(UserMessage(text=prompt)):
                if isinstance(ev, TextDelta):
                    yield ev.content
                elif isinstance(ev, StreamDone):
                    break
            return
        except Exception as ex:
            logger.error(f"Emergent chat stream error: {ex}")

    # Fallback response
    yield f"Petra AI Assistant [{FEATHERLESS_MODEL}]: Real-time paper trading on Alpaca is active. Deterministic risk gates and options strike engines are fully operational."

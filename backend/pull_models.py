"""Pull and categorize relevant models from Featherless AI."""
import httpx
import json
import re
from pathlib import Path
from dotenv import load_dotenv
import os

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

KEY = os.environ.get("FEATHERLESS_API_KEY")
if not KEY:
    print("Error: FEATHERLESS_API_KEY environment variable not set.")
    exit(1)
URL = "https://api.featherless.ai/v1/models"

print(f"Connecting to {URL}...")
r = httpx.get(URL, headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
if r.status_code != 200:
    print(f"Failed to fetch models: {r.status_code} {r.text}")
    exit(1)

data = r.json().get("data", [])
print(f"Total models available on Featherless: {len(data):,}")

# Filter out gated models that require manual HuggingFace verification
available = [m for m in data if not m.get("is_gated", False)]
print(f"Total accessible (ungated) models: {len(available):,}")

def filter_models(pattern, limit=15):
    matches = []
    for m in available:
        if re.search(pattern, m["id"], re.I):
            matches.append({
                "id": m["id"],
                "context_length": m.get("context_length", "N/A"),
                "max_completion_tokens": m.get("max_completion_tokens", "N/A"),
                "model_class": m.get("model_class", "text-generation")
            })
            if len(matches) >= limit:
                break
    return matches

categories = {
    "qwen_3_flagship": {
        "title": "Qwen 3.x Family (Advanced & Thinking Models)",
        "description": "Recommended for deep market regime reasoning and structured analysis",
        "models": filter_models(r"^qwen/qwen3(\.[0-9]+)?", limit=12)
    },
    "qwen_2_instruct": {
        "title": "Qwen 2 / 2.5 Instruct (High-Speed Structured JSON)",
        "description": "Ideal for low-latency signal extraction and strict schema output",
        "models": filter_models(r"^qwen/qwen2(\.5)?.*instruct", limit=8)
    },
    "deepseek_reasoning": {
        "title": "DeepSeek R1 / V3 Reasoning",
        "description": "Chain-of-thought financial reasoning and risk scenario analysis",
        "models": filter_models(r"deepseek.*(r1|v3)", limit=8)
    },
    "mistral_enterprise": {
        "title": "Mistral Enterprise Models",
        "description": "Reliable multi-lingual and deterministic instructions",
        "models": filter_models(r"^mistralai/mistral-(large|medium|small)", limit=6)
    },
    "finance_specialized": {
        "title": "Finance & Market Domain Models",
        "description": "Fine-tuned specifically for financial data, stock prediction, and numeric reasoning",
        "models": filter_models(r"(finance|financial|stockdirection|trading)", limit=10)
    }
}

output_path = ROOT_DIR / "featherless_models.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(categories, f, indent=2)

print(f"\nSaved curated models to {output_path.name}:")
for key, cat in categories.items():
    print(f"\n--- {cat['title']} ({len(cat['models'])} models) ---")
    for m in cat["models"][:5]:
        print(f"  * {m['id']} | Context: {m['context_length']}")

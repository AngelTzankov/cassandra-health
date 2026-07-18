"""LLM narrative layer for Cassandra.

Turns deterministically-computed features into a clinician-facing narrative.
The model ONLY writes prose grounded on the numbers it is given — it never
invents values, decides the triage state, or overrides the rules. If no API
key is present or anything fails, callers fall back to the deterministic template.
"""

import json
import os
import urllib.request

MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")
ENDPOINT = "https://api.openai.com/v1/chat/completions"

SYSTEM = (
    "You are the narrative layer of a clinical monitoring triage tool for heart-failure "
    "at-home care. You are given deterministically-computed wearable features and a triage "
    "state that has ALREADY been decided by rules. Your ONLY job is to write the clinician-facing "
    "prose: a one-sentence headline and a short 'assessment' paragraph. "
    "STRICT RULES: (1) Use ONLY the numbers provided — never invent or estimate values. "
    "(2) Do NOT change or question the triage state. (3) Write for a clinician: concise, factual, "
    "no hedging fluff, no patient-directed advice. (4) When signals converge, explain the pattern "
    "(e.g. fluid retention) and note what argues against alternatives (e.g. flat temperature vs infection). "
    "(5) For a stable/green patient, write a brief reassuring all-clear. "
    "Return STRICT JSON: {\"headline\": \"...\", \"assessment\": \"...\"}."
)


def available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def generate_narrative(features: dict, state: str, negatives: list) -> dict | None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    payload = {
        "model": MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({
                "triage_state": state,
                "computed_features": features,
                "deterministic_findings": negatives,
            })},
        ],
    }
    try:
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        content = resp["choices"][0]["message"]["content"]
        out = json.loads(content)
        if "headline" in out and "assessment" in out:
            return {"headline": out["headline"].strip(), "hypothesis": out["assessment"].strip()}
    except Exception as e:
        print(f"[llm] narrative generation failed, falling back to template: {e}")
    return None

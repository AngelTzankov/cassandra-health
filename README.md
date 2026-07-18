# Cassandra — the warning, finally heard

**Early-warning triage for chronic heart failure, from the wearable you already own.**

🔗 **Live demo:** https://cassandra-health.fly.dev
🩺 **Clinician dashboard:** https://cassandra-health.fly.dev/dashboard

---

Heart-failure decompensation announces itself 5–10 days before a hospital admission — resting
heart rate creeps up, breathing quickens, activity drops, weight climbs, oxygen drifts down.
Millions of patients already wear a device recording exactly these signals. Almost no one looks.

In Greek myth, **Cassandra** could see catastrophe coming — but was cursed so no one believed her.
Every heart-failure patient wearing a smartwatch is a Cassandra: the warning is right, and unheard.
This is the cure for the curse.

## What it does

- Reads a patient's Apple Health export (Apple Watch, Whoop, Garmin, smart scale — all supported).
- Detects the **convergence** of decompensation signals against each patient's *personal baseline* —
  catching several small drifts before any single one crosses a threshold.
- Classifies every patient **STABLE / REVIEW SOON / ACT TODAY** and writes the clinician a single,
  evidenced case — turning 20 minutes of stream-reading into a 2-minute validated decision.

## Architecture — rules decide, AI explains

1. **Deterministic rules** detect decompensation and set the triage state. Auditable, reproducible,
   personal-baseline, debounced. The rules own the alert — the AI never does.
2. **GPT-4o** writes the clinician-facing narrative from the *computed* numbers and the *already-decided*
   state. It cannot invent a value, change the state, or raise an alert. Its worst failure is bad prose.
3. **A clinician** validates a written conclusion with the evidence one tap away.

## Run locally

```bash
python3 -m venv .venv && ./.venv/bin/pip install fastapi uvicorn qrcode pillow python-multipart
export OPENAI_API_KEY=sk-...        # optional — falls back to deterministic templates if absent
./.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 and upload an Apple Health export (Health app → profile → Export All Health Data).

## Stack

Python · FastAPI · streaming Apple Health XML parser · personal-baseline rule engine ·
OpenAI GPT-4o (narrative layer only) · Fly.io.

## Evidence base

The mechanism is proven: CardioMEMS (−37% HF admissions), HeartLogic (decompensation predicted from
HR/respiration/activity ~34 days out), LINK-HF (consumer sensors, ~6.5 days out), TIM-HF2 (−30%
mortality with at-home telemonitoring). Cassandra delivers the proven thing at wristband cost, passively,
with a triage layer that doesn't drown clinicians in false alarms.

---

*Built at the Reimagine Health hackathon. A prototype — not a medical device, not a substitute for clinical care.*

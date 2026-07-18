import json, random, os
import datetime as dt
random.seed(42)

d0 = dt.date(2026, 6, 9)
N = 40
onset = 34

def series(base, jitter, rise_from=None, rise_to=0):
    out = []
    for i in range(N):
        v = base + random.uniform(-jitter, jitter)
        if rise_from is not None and i >= rise_from:
            v += rise_to * (i - rise_from) / (N - 1 - rise_from)
        out.append(v)
    return out

dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(N)]
rhr_vals  = [round(v) for v in series(68, 1.6, onset, 9)]
resp_vals = [round(v, 1) for v in series(15.0, 0.4, onset, 3.2)]
steps_vals = [int(v) for v in series(4200, 500, onset, -2600)]
spo2_vals = [round(v, 1) for v in series(96.5, 0.5, onset, -3.3)]
wt = []
w = 71.0
for i in range(N):
    if i >= onset:
        w += 0.38
    wt.append(round(w + random.uniform(-0.15, 0.15), 1))
temp_vals = [round(v, 2) for v in series(35.9, 0.08)]

ellen = {
  "id": "ellen-w", "name": "Ellen W.", "age": 72, "sex": "F",
  "program": "Heart-failure virtual ward — NYHA II, 6 weeks post-discharge",
  "state": "amber", "state_reason": "HF-DECOMP-EARLY",
  "monitoring": {"days_monitored": 208, "wear_pct": 86, "data_current_as_of": "2026-07-18 06:40",
                 "sources": "Apple Watch SE + connected scale · Apple Health"},
  "headline": "Five-day pattern consistent with early cardiac decompensation: resting HR +9 bpm, nocturnal respiratory rate +3.2/min, activity down ~60%, weight +1.9 kg, and a downward SpO2 drift — all beginning 13 July. Temperature flat, arguing against infection. She has not reported feeling unwell.",
  "hypothesis": "Stable for months (RHR 68, respiratory rate 15.0, weight 71 kg). From 13 July, five independent signals moved together in the direction of fluid retention: resting heart rate and nocturnal respiratory rate rose, daily activity fell by roughly 60%, weight climbed 1.9 kg over five days, and mean overnight SpO2 drifted from 96–97% toward 93%. Flat wrist temperature and absence of any infective signal argue against a chest infection as the driver. The convergent pattern — autonomic and respiratory drift leading, weight and oxygenation confirming — is the classic early decompensation prodrome, typically 5–10 days ahead of symptomatic congestion. Acting now, at home, plausibly prevents an admission.",
  "negatives": [
    "Wrist temperature flat — argues against intercurrent chest infection as the cause",
    "No arrhythmia or irregular-rhythm notifications",
    "Sleep duration preserved — resting, not acutely distressed",
    "Pattern is 5 days old and pre-symptomatic — the intervention window is open"
  ],
  "would_change": [
    "Weight gain exceeding 2 kg / 3 days or accelerating — escalate to same-day review",
    "New breathlessness, orthopnoea or ankle swelling on contact — urgent review",
    "SpO2 sustained below 92% — escalate regardless of other signals"
  ],
  "suggested_step": "Same-day telephone review per HF virtual-ward protocol: symptoms, orthopnoea, ankle oedema, adherence. Consider diuretic uptitration per standing protocol and daily weights until stable. This is a phone call and a dose change today — versus an emergency admission in 5–10 days if the trajectory continues.",
  "rule_fired": {"id": "HF-DECOMP-EARLY",
                 "detail": "≥3 of {RHR ≥ +5 bpm, resp rate ≥ +2/min, activity ↓ ≥30%} vs 60-day personal baseline, sustained ≥3 days, WITH ≥1 confirmatory {weight ↑, SpO2 drift} (fired 18 Jul 2026, 06:30)"},
  "rhr_series": {"start_date": d0.isoformat(), "values": rhr_vals, "baseline": 68, "unit": "bpm"},
  "resp_series": {"dates": dates, "values": resp_vals, "baseline": 15.0},
  "steps_series": steps_vals, "steps_dates": dates,
  "spo2_series": {"dates": dates, "values": spo2_vals},
  "weight_series": {"dates": dates, "values": wt},
  "wrist_series": {"dates": dates, "values": temp_vals, "baseline": 35.9},
  "rhr_markers": [{"i": onset, "color": "#ff9830", "label": "Decompensation onset — 13 Jul"}],
  "provenance": {"cleared": "Irregular-rhythm / high-low HR notifications (FDA-cleared, Apple)",
                 "estimates": "Resting HR, respiratory rate, SpO2 (trend only, wellness-grade), activity, weight (connected scale)",
                 "not_assessed": "Auscultation, JVP, natriuretic peptides — clinical contact required to confirm"}
}
json.dump(ellen, open('fixtures/ellen-w.json', 'w'), indent=1)
print("Ellen rebuilt as HF hero, state:", ellen["state"], ellen["state_reason"])

relabel = {
  "james-o": ("James O.", "Heart-failure caseload — NYHA II, stable"),
  "sofia-b": ("Sofia B.", "Heart-failure caseload — NYHA I, post-titration"),
  "dev-p":   ("Dev P.",   "Heart-failure caseload — NYHA II, stable"),
  "david-r": ("David R.", "Heart-failure caseload — AF + HF, rate-controlled"),
}
for pid, (nm, prog) in relabel.items():
    fp = f"fixtures/{pid}.json"
    if os.path.exists(fp):
        p = json.load(open(fp))
        p["name"], p["program"], p["state"] = nm, prog, "green"
        days = p["monitoring"].get("days_monitored", "—")
        wear = p["monitoring"].get("wear_pct", 88)
        p["headline"] = f"{days} days monitored, worn {wear}%. No decompensation signals — resting HR, respiratory rate and weight stable. Nothing requires review."
        for k in ("hypothesis",):
            p.pop(k, None)
        json.dump(p, open(fp, "w"), indent=1)
        print("greened:", pid)

# retire off-theme demo fixtures
for pid in ("maria", "alex", "live-8j4moQ"):
    for fp in (f"fixtures/{pid}.json",):
        if os.path.exists(fp):
            os.remove(fp); print("removed:", fp)

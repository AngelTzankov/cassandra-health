import json, random
import datetime as dt
random.seed(5)

d0 = dt.date(2026, 6, 9)
N = 40
onset = 31  # earlier onset, further progressed by "now"
dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(N)]

def ramp(base, jitter, rise_from, rise_to):
    out = []
    for i in range(N):
        v = base + random.uniform(-jitter, jitter)
        if i >= rise_from:
            v += rise_to * (i - rise_from) / (N - 1 - rise_from)
        out.append(v)
    return out

rhr  = [int(round(v)) for v in ramp(70, 1.6, onset, 18)]     # 70 -> ~88
resp = [round(v,1) for v in ramp(15.2, 0.4, onset, 6.0)]     # 15 -> ~21
steps = [int(v) for v in ramp(3600, 400, onset, -2900)]      # collapse to ~700
spo2 = [round(v,1) for v in ramp(96.0, 0.5, onset, -6.5)]    # 96 -> ~89.5 (sustained <92 = red)
wt = []
w = 69.0
for i in range(N):
    if i >= onset: w += 0.7
    wt.append(round(w + random.uniform(-0.15,0.15), 1))       # +~3.6 kg
temp = [round(v,2) for v in ramp(35.9, 0.08, onset, 0.0)]     # flat — still not infection

red = {
  "id": "dimitra-v", "name": "Dimitra Vlachou", "age": 76, "sex": "F",
  "program": "Heart-failure virtual ward — NYHA III, high-risk",
  "state": "red", "state_reason": "HF-DECOMP-SEVERE",
  "monitoring": {"days_monitored": 141, "wear_pct": 88, "data_current_as_of": "2026-07-18 06:15",
                 "sources": "Apple Watch + connected scale · Apple Health"},
  "headline": "Advancing decompensation over 9 days now crossing a severe threshold: resting HR +18 bpm, respiratory rate +6/min, weight +3.6 kg, and mean overnight SpO2 sustained below 92% for three nights. Activity near-zero. Temperature flat. Same-day clinical review required.",
  "hypothesis": "This is the same fluid-retention pattern as an early amber alert — but further along and now crossing a hard safety threshold. Since 10 July all congestion signals have escalated: weight is up 3.6 kg (well beyond the 2 kg / 3-day threshold), respiratory rate has risen 6/min, resting HR 18 bpm, and — critically — mean overnight oxygen saturation has fallen below 92% for three consecutive nights, indicating gas-exchange compromise. Flat temperature again argues against infection. Without intervention today this trajectory ends in an emergency admission, likely within 48 hours. Caught now, it is a planned same-day review and probably IV diuretics — not a 999 call from a collapse.",
  "negatives": [
    "Temperature flat — not an infective exacerbation",
    "No arrhythmia notifications — this is congestion, not a primary rhythm event",
    "Pattern is 9 days of continuous, one-directional escalation — unambiguous, not noise"
  ],
  "would_change": [
    "This is already the escalation ceiling for home monitoring — the next step is clinical, today",
    "SpO2 continuing to fall or new resting breathlessness — consider emergency pathway now"
  ],
  "suggested_step": "SAME-DAY clinical review — telephone within the hour, face-to-face or ambulatory HF unit today. Likely IV or high-dose diuretics; assess need for admission vs hospital-at-home. The value here is that this is a planned, daytime, resourced intervention caught 24–48 h before it would otherwise present as an emergency.",
  "rule_fired": {"id": "HF-DECOMP-SEVERE",
                 "detail": "Mean overnight SpO2 < 92% sustained ≥ 3 nights, WITH active decompensation pattern (weight ↑ > 2 kg/3 days) (fired 18 Jul 2026, 06:15)"},
  "rhr_series": {"start_date": d0.isoformat(), "values": rhr, "baseline": 70, "unit": "bpm"},
  "resp_series": {"dates": dates, "values": resp, "baseline": 15.2},
  "steps_series": steps, "steps_dates": dates,
  "spo2_series": {"dates": dates, "values": spo2},
  "weight_series": {"dates": dates, "values": wt},
  "wrist_series": {"dates": dates, "values": temp, "baseline": 35.9},
  "symptom_log": [{"ts": "2026-07-17 22:10", "kind": "Shortness of breath"},
                  {"ts": "2026-07-16 07:30", "kind": "Fatigue"}],
  "rhr_markers": [{"i": onset, "color": "#f2495c", "label": "Decompensation onset — 10 Jul"}],
  "provenance": {"cleared": "Irregular-rhythm / high-low HR notifications (FDA-cleared, Apple)",
                 "estimates": "Resting HR, respiratory rate, SpO2 (trend), activity, weight (connected scale)",
                 "not_assessed": "Auscultation, JVP, natriuretic peptides, chest imaging — urgent clinical assessment required"}
}
json.dump(red, open('fixtures/dimitra-v.json','w'), indent=1)
print("RED case seeded:", red["name"], "| SpO2 floor:", min(spo2), "| weight gain:", round(wt[-1]-wt[0],1), "kg")

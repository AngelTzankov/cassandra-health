import json, random, os
import datetime as dt
random.seed(7)

d0 = dt.date(2026, 6, 9)
N = 40
dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(N)]


def flat(base, jitter, dec=0):
    return [round(base + random.uniform(-jitter, jitter), dec) if dec else int(base + random.uniform(-jitter, jitter))
            for _ in range(N)]


def green_patient(pid, name, age, sex, program, rhr_base, resp_base, steps_base, weight_base, spo2_base,
                  days, wear, note):
    rhr = [int(round(v)) for v in [rhr_base + random.uniform(-2, 2) for _ in range(N)]]
    resp = flat(resp_base, 0.5, 1)
    steps = flat(steps_base, steps_base * 0.18)
    wt = [round(weight_base + random.uniform(-0.3, 0.3), 1) for _ in range(N)]
    spo2 = flat(spo2_base, 0.6, 1)
    sleep_hours = flat(7.0, 0.7, 1)
    return {
        "id": pid, "name": name, "age": age, "sex": sex, "generated": True,
        "state": "green", "program": program,
        "monitoring": {"record_total": random.randint(120000, 480000), "days_monitored": days,
                       "date_range": {"start": "2026-01-01", "end": "2026-07-18"},
                       "sources": "Apple Watch + connected scale · Apple Health", "wear_pct": wear},
        "headline": (f"{days} days monitored, worn {wear}%. No decompensation signals — resting HR, respiratory rate, "
                     f"weight and oxygen saturation all stable at personal baseline. {note} Nothing requires review."),
        "negatives": [
            f"Resting HR stable at {min(rhr)}–{max(rhr)} bpm (baseline {rhr_base})",
            f"Nocturnal respiratory rate stable ({resp_base}/min) — no upward drift",
            f"Weight steady at ~{weight_base} kg — no fluid-retention trend",
            f"SpO2 holding ~{spo2_base}% — no downward drift",
            "No irregular-rhythm, high/low-HR or sleep-apnoea notifications",
        ],
        "context": [
            f"Activity ~{int(sum(steps)/len(steps)):,} steps/day — consistent with baseline function",
            f"Sleep averaging {round(sum(sleep_hours)/len(sleep_hours),1)} h/night",
        ],
        "events": {"irregular_rhythm": 0, "high_hr": 0, "low_hr": 0, "sleep_apnea": 0},
        "ecg_count": 0, "ecgs": [], "event_log": [], "symptom_log": [],
        "rhr_series": {"values": rhr, "dates": dates, "baseline": rhr_base, "unit": "bpm"},
        "resp_series": {"dates": dates, "values": resp, "baseline": resp_base},
        "steps_series": steps, "steps_dates": dates,
        "weight_series": {"dates": dates, "values": wt},
        "spo2_series": {"dates": dates, "values": spo2},
        "sleep_series": {"dates": dates, "hours": sleep_hours, "basis": "asleep"},
        "provenance": {
            "cleared": "Irregular-rhythm / high-low HR / sleep-apnoea notifications (FDA-cleared, Apple)",
            "estimates": "Resting HR, respiratory rate, SpO2 (trend), weight (connected scale), activity",
            "not_assessed": "Auscultation, JVP, natriuretic peptides — clinical contact required",
        },
    }


greens = [
    green_patient("margaret-h", "Margaret H.", 74, "F", "Heart-failure virtual ward — NYHA II, stable",
                  66, 15, 3800, 68.0, 96, 181, 90, "Six weeks stable since discharge."),
    green_patient("arthur-p", "Arthur P.", 69, "M", "Heart-failure caseload — NYHA I, post-titration",
                  61, 14, 6200, 82.0, 97, 154, 88, "Responding well to titration."),
    green_patient("doreen-k", "Doreen K.", 78, "F", "Heart-failure caseload — NYHA II, stable",
                  70, 16, 2400, 61.0, 95, 233, 84, "Long-term stable."),
    green_patient("raymond-t", "Raymond T.", 66, "M", "Heart-failure caseload — AF + HF, rate-controlled",
                  64, 15, 5100, 90.0, 96, 198, 91, "Rate control holding."),
    green_patient("brenda-s", "Brenda S.", 71, "F", "Heart-failure caseload — NYHA II, stable",
                  67, 15, 3300, 74.0, 96, 167, 87, "No concerns this quarter."),
]

# remove the old relabelled leftovers
for pid in ("james-o", "sofia-b", "dev-p", "david-r"):
    fp = f"fixtures/{pid}.json"
    if os.path.exists(fp):
        os.remove(fp); print("removed old:", pid)

for g in greens:
    json.dump(g, open(f"fixtures/{g['id']}.json", "w"), indent=1)
    print("seeded green:", g["id"], g["name"])

print("done — caseload:", [f[:-5] for f in os.listdir("fixtures") if f.endswith(".json")])

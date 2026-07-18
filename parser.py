"""Streaming parser for Apple Health export.zip → feature JSON (brief schema)."""

import datetime as dt
import statistics
import zipfile
from collections import Counter, defaultdict
from xml.etree.ElementTree import iterparse

RHR = "HKQuantityTypeIdentifierRestingHeartRate"
HR = "HKQuantityTypeIdentifierHeartRate"
STEPS = "HKQuantityTypeIdentifierStepCount"
VO2 = "HKQuantityTypeIdentifierVO2Max"
HRR1 = "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute"
RESP = "HKQuantityTypeIdentifierRespiratoryRate"
SLEEP = "HKCategoryTypeIdentifierSleepAnalysis"
IRREGULAR = "HKCategoryTypeIdentifierIrregularHeartRhythmEvent"
HIGH_HR = "HKCategoryTypeIdentifierHighHeartRateEvent"
LOW_HR = "HKCategoryTypeIdentifierLowHeartRateEvent"
SLEEP_APNEA = "HKCategoryTypeIdentifierSleepApneaEvent"
WALK_HR = "HKQuantityTypeIdentifierWalkingHeartRateAverage"
SIXMIN = "HKQuantityTypeIdentifierSixMinuteWalkTestDistance"
EXTIME = "HKQuantityTypeIdentifierAppleExerciseTime"
AFIB_B = "HKQuantityTypeIdentifierAtrialFibrillationBurden"
WRIST_T = "HKQuantityTypeIdentifierAppleSleepingWristTemperature"
MASS = "HKQuantityTypeIdentifierBodyMass"
BP_SYS = "HKQuantityTypeIdentifierBloodPressureSystolic"
BP_DIA = "HKQuantityTypeIdentifierBloodPressureDiastolic"
STEADY = "HKQuantityTypeIdentifierAppleWalkingSteadiness"
SPO2 = "HKQuantityTypeIdentifierOxygenSaturation"
BODYFAT = "HKQuantityTypeIdentifierBodyFatPercentage"

EVENT_LABELS = {
    IRREGULAR: "Irregular rhythm notification",
    HIGH_HR: "High heart-rate notification",
    LOW_HR: "Low heart-rate notification",
    SLEEP_APNEA: "Sleep apnoea notification",
}

# symptom category types worth surfacing (cardiac-adjacent)
SYMPTOM_KEYS = {
    "RapidPoundingOrFlutteringHeartbeat": "Palpitations",
    "SkippedHeartbeat": "Skipped heartbeat",
    "ChestTightnessOrPain": "Chest tightness/pain",
    "Dizziness": "Dizziness",
    "Fainting": "Fainting",
    "ShortnessOfBreath": "Shortness of breath",
    "Fatigue": "Fatigue",
}


def _ts(elem, attr="startDate"):
    raw = elem.get(attr, "")
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


def parse_export(zip_path: str) -> dict:
    """Stream-parse export.zip. Never loads the full XML into RAM."""
    counts = Counter()
    daily_rhr: dict[str, list[float]] = defaultdict(list)
    daily_steps: Counter = Counter()
    daily_resp: dict[str, list[float]] = defaultdict(list)
    daily_walkhr: dict[str, list[float]] = defaultdict(list)
    daily_extime: Counter = Counter()
    daily_wrist: dict[str, list[float]] = defaultdict(list)
    daily_mass: dict[str, float] = {}
    daily_bps: dict[str, list[float]] = defaultdict(list)
    daily_bpd: dict[str, list[float]] = defaultdict(list)
    sixmin: list[tuple[str, float]] = []
    afib_b: list[tuple[str, float]] = []
    sleep_secs: Counter = Counter()
    inbed_secs: Counter = Counter()
    daily_steady: dict[str, list[float]] = defaultdict(list)
    daily_spo2: dict[str, list[float]] = defaultdict(list)
    daily_fat: dict[str, float] = {}
    sleep_sources: Counter = Counter()
    hr_values: list[float] = []
    vo2: list[tuple[str, float]] = []
    hrr: list[tuple[str, float]] = []
    event_log: list[dict] = []
    symptom_log: list[dict] = []
    events = {IRREGULAR: 0, HIGH_HR: 0, LOW_HR: 0, SLEEP_APNEA: 0}
    dates_seen: set[str] = set()
    min_date, max_date = None, None
    sources: Counter = Counter()

    zf = zipfile.ZipFile(zip_path)
    xml_name = next((n for n in zf.namelist() if n.endswith("export.xml")), None)
    if not xml_name:
        raise ValueError("export.xml not found in zip")
    ecg_files = [n for n in zf.namelist() if "/electrocardiograms/" in n and n.endswith(".csv")]

    with zf.open(xml_name) as fh:
        for _, elem in iterparse(fh, events=("end",)):
            if elem.tag != "Record":
                elem.clear()
                continue
            rtype = elem.get("type", "")
            counts[rtype] += 1
            start = elem.get("startDate", "")
            day = start[:10]
            if day:
                dates_seen.add(day)
                if min_date is None or day < min_date:
                    min_date = day
                if max_date is None or day > max_date:
                    max_date = day
            src = elem.get("sourceName")
            if src:
                sources[src] += 1
            try:
                if rtype == RHR:
                    daily_rhr[day].append(float(elem.get("value")))
                elif rtype == HR:
                    hr_values.append(float(elem.get("value")))
                elif rtype == STEPS:
                    daily_steps[day] += float(elem.get("value"))
                elif rtype == VO2:
                    vo2.append((day, float(elem.get("value"))))
                elif rtype == HRR1:
                    hrr.append((day, float(elem.get("value"))))
                elif rtype == RESP:
                    daily_resp[day].append(float(elem.get("value")))
                elif rtype == WALK_HR:
                    daily_walkhr[day].append(float(elem.get("value")))
                elif rtype == SIXMIN:
                    sixmin.append((day, float(elem.get("value"))))
                elif rtype == EXTIME:
                    daily_extime[day] += float(elem.get("value"))
                elif rtype == AFIB_B:
                    afib_b.append((day, float(elem.get("value"))))
                elif rtype == WRIST_T:
                    daily_wrist[day].append(float(elem.get("value")))
                elif rtype == MASS:
                    daily_mass[day] = float(elem.get("value"))
                elif rtype == BP_SYS:
                    daily_bps[day].append(float(elem.get("value")))
                elif rtype == BP_DIA:
                    daily_bpd[day].append(float(elem.get("value")))
                elif rtype == SLEEP:
                    val = elem.get("value", "")
                    if "Asleep" in val or "InBed" in val:
                        t0, t1 = _ts(elem, "startDate"), _ts(elem, "endDate")
                        if t0 and t1:
                            night = t1.date().isoformat()
                            bucket = sleep_secs if "Asleep" in val else inbed_secs
                            bucket[night] += (t1 - t0).total_seconds()
                            if src:
                                sleep_sources[src] += 1
                elif rtype == STEADY:
                    daily_steady[day].append(float(elem.get("value")))
                elif rtype == SPO2:
                    daily_spo2[day].append(float(elem.get("value")))
                elif rtype == BODYFAT:
                    daily_fat[day] = float(elem.get("value"))
                elif rtype in events:
                    events[rtype] += 1
                    event_log.append({"ts": start[:16], "kind": EVENT_LABELS[rtype]})
                else:
                    for key, label in SYMPTOM_KEYS.items():
                        if rtype.endswith(key):
                            symptom_log.append({"ts": start[:16], "kind": label})
                            break
            except (TypeError, ValueError):
                pass
            elem.clear()

    # ---- ECG strips (latest 3, real voltages, downsampled for SVG)
    ecgs = []
    for name in ecg_files:
        try:
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8", errors="ignore")
            meta, volts = {}, []
            for line in text.splitlines():
                parts = line.split(",", 1)
                if len(parts) == 2 and not _is_float(parts[0]):
                    meta[parts[0].strip()] = parts[1].strip()
                elif parts and _is_float(parts[0]):
                    volts.append(float(parts[0]))
            if not volts:
                continue
            stride = max(1, len(volts) // 1100)
            ecgs.append({
                "date": meta.get("Recorded Date", "")[:16] or name.split("/")[-1],
                "classification": meta.get("Classification", "Unclassified"),
                "symptoms": meta.get("Symptoms", "") or "None reported",
                "sample_rate": meta.get("Sample Rate", ""),
                "points": [round(v, 1) for v in volts[::stride]],
            })
        except Exception:
            continue
    ecgs.sort(key=lambda e: e["date"], reverse=True)
    ecgs = ecgs[:3]

    # ---- derived features
    rhr_daily = sorted((d, statistics.mean(v)) for d, v in daily_rhr.items() if v)
    rhr_last = rhr_daily[-60:]
    rhr_series = [round(v, 1) for _, v in rhr_last]
    rhr_dates = [d for d, _ in rhr_last]
    rhr_baseline = round(statistics.median(v for _, v in rhr_daily[-90:]), 1) if rhr_daily else None

    steps_daily = sorted(daily_steps.items())
    steps_last = steps_daily[-60:]

    vo2.sort()
    vo2_trend = None
    if len(vo2) >= 4:
        half = len(vo2) // 2
        early = statistics.mean(v for _, v in vo2[:half])
        late = statistics.mean(v for _, v in vo2[half:])
        direction = "declining" if late < early - 1 else ("improving" if late > early + 1 else "stable")
        vo2_trend = {"latest": round(vo2[-1][1], 1), "direction": direction,
                     "early": round(early, 1), "late": round(late, 1)}

    hrr_avg = round(statistics.mean(v for _, v in hrr[-20:]), 0) if hrr else None

    sleep_basis = "asleep" if sleep_secs else ("in_bed" if inbed_secs else None)
    sleep_nights = sorted((sleep_secs or inbed_secs).items())[-30:]
    sleep_summary = None
    if len(sleep_nights) >= 5:
        hours = [min(s / 3600, 16) for _, s in sleep_nights]
        sleep_summary = {"avg_h": round(statistics.mean(hours), 1),
                         "sd_h": round(statistics.stdev(hours), 1) if len(hours) > 1 else 0,
                         "nights": len(hours), "basis": sleep_basis}

    resp_daily = sorted((d, statistics.mean(v)) for d, v in daily_resp.items() if v)
    resp_summary = None
    if len(resp_daily) >= 14:
        base = statistics.median(v for _, v in resp_daily[:-7])
        recent = statistics.mean(v for _, v in resp_daily[-7:])
        resp_summary = {"baseline": round(base, 1), "recent_7d": round(recent, 1),
                        "deviation": round(recent - base, 1)}

    walkhr_daily = sorted((d, statistics.mean(v)) for d, v in daily_walkhr.items() if v)[-60:]
    sixmin.sort()
    extime_daily = sorted(daily_extime.items())
    # weekly exercise minutes (ISO week label = monday date)
    week_mins: Counter = Counter()
    for d, mins in extime_daily:
        try:
            day_dt = dt.date.fromisoformat(d)
            monday = (day_dt - dt.timedelta(days=day_dt.weekday())).isoformat()
            week_mins[monday] += mins
        except ValueError:
            pass
    extime_weekly = sorted(week_mins.items())[-12:]
    afib_b.sort()
    wrist_daily = sorted((d, statistics.mean(v)) for d, v in daily_wrist.items() if v)[-30:]
    wrist_baseline = round(statistics.median(v for _, v in wrist_daily), 2) if len(wrist_daily) >= 7 else None
    mass_daily = sorted(daily_mass.items())[-90:]
    bp_daily = sorted((d, statistics.mean(v)) for d, v in daily_bps.items() if v)[-60:]
    bpd_latest = None
    if daily_bpd:
        last_day = sorted(daily_bpd)[-1]
        bpd_latest = round(statistics.mean(daily_bpd[last_day]), 0)

    spo2_daily = sorted((d, statistics.mean(v)) for d, v in daily_spo2.items() if v)[-60:]
    spo2_vals = [v * 100 if v <= 1.01 else v for _, v in spo2_daily]
    fat_daily = sorted(daily_fat.items())[-90:]
    fat_vals = [v * 100 if v <= 1.01 else v for _, v in fat_daily]

    total_days = 0
    if min_date and max_date:
        total_days = (dt.date.fromisoformat(max_date) - dt.date.fromisoformat(min_date)).days + 1

    event_log.sort(key=lambda e: e["ts"], reverse=True)
    symptom_log.sort(key=lambda e: e["ts"], reverse=True)

    return {
        "record_total": sum(counts.values()),
        "record_types": dict(counts.most_common(15)),
        "date_range": {"start": min_date, "end": max_date, "total_days": total_days},
        "days_with_data": len(dates_seen),
        "sources": [s for s, _ in sources.most_common(5)],
        "has_watch_data": bool(daily_rhr or hr_values),
        "ecg_count": len(ecg_files),
        "ecgs": ecgs,
        "events": {"irregular_rhythm": events[IRREGULAR], "high_hr": events[HIGH_HR],
                   "low_hr": events[LOW_HR], "sleep_apnea": events[SLEEP_APNEA]},
        "event_log": event_log[:12],
        "symptom_log": symptom_log[:12],
        "rhr": {"series_60d": rhr_series, "dates_60d": rhr_dates,
                "baseline_90d": rhr_baseline, "days_covered": len(daily_rhr) or None},
        "hr_sample_count": len(hr_values),
        "hr_max": max(hr_values) if hr_values else None,
        "vo2max": vo2_trend,
        "vo2_series": {"dates": [d for d, _ in vo2], "values": [round(v, 1) for _, v in vo2]},
        "sleep_series": {"dates": [d for d, _ in sleep_nights],
                         "hours": [round(min(s / 3600, 16), 1) for _, s in sleep_nights],
                         "basis": sleep_basis},
        "steady_series": _steady_series(daily_steady),
        "resp_series": {"dates": [d for d, _ in resp_daily[-30:]],
                        "values": [round(v, 1) for _, v in resp_daily[-30:]],
                        "baseline": resp_summary["baseline"] if resp_summary else None},
        "hr_recovery_avg": hrr_avg,
        "sleep": sleep_summary,
        "respiratory": resp_summary,
        "walkhr_series": {"dates": [d for d, _ in walkhr_daily], "values": [round(v, 1) for _, v in walkhr_daily]},
        "sixmin_series": {"dates": [d for d, _ in sixmin], "values": [round(v, 0) for _, v in sixmin]},
        "extime_weekly": {"dates": [d for d, _ in extime_weekly], "values": [round(v, 0) for _, v in extime_weekly]},
        "afib_series": {"dates": [d for d, _ in afib_b], "values": [round(v, 1) for _, v in afib_b]},
        "wrist_series": {"dates": [d for d, _ in wrist_daily], "values": [round(v, 2) for _, v in wrist_daily],
                         "baseline": wrist_baseline},
        "weight_series": {"dates": [d for d, _ in mass_daily], "values": [round(v, 1) for _, v in mass_daily]},
        "weight_last": {"value": round(mass_daily[-1][1], 1), "date": mass_daily[-1][0]} if mass_daily else None,
        "bp_series": {"dates": [d for d, _ in bp_daily], "values": [round(v, 0) for _, v in bp_daily],
                      "dia_latest": bpd_latest},
        "spo2_series": {"dates": [d for d, _ in spo2_daily], "values": [round(v, 1) for v in spo2_vals]},
        "fat_series": {"dates": [d for d, _ in fat_daily], "values": [round(v, 1) for v in fat_vals]},
        "steps": {"series_60d": [int(v) for _, v in steps_last],
                  "dates_60d": [d for d, _ in steps_last],
                  "daily_avg_60d": int(statistics.mean(v for _, v in steps_last)) if steps_last else None},
    }


def _steady_series(daily_steady: dict) -> dict:
    days = sorted((d, sum(v) / len(v)) for d, v in daily_steady.items() if v)[-60:]
    if not days:
        return {}
    vals = [v for _, v in days]
    if max(vals) <= 1.01:  # exported as 0-1 fraction
        vals = [v * 100 for v in vals]
    return {"dates": [d for d, _ in days], "values": [round(v, 0) for v in vals]}


MARKER_COLORS = {
    "Irregular rhythm notification": "#c0392b",
    "High heart-rate notification": "#d99a2b",
    "Low heart-rate notification": "#d99a2b",
    "Sleep apnoea notification": "#2b6cb3",
}


def _rhr_markers(f: dict) -> list:
    """Map notification events and logged symptoms onto RHR chart indices."""
    dates = f["rhr"].get("dates_60d", [])
    if not dates:
        return []
    idx = {d: i for i, d in enumerate(dates)}
    markers = []
    for ev in f.get("event_log", []):
        i = idx.get(ev["ts"][:10])
        if i is not None:
            markers.append({"i": i, "color": MARKER_COLORS.get(ev["kind"], "#c0392b"), "label": ev["kind"]})
    for sy in f.get("symptom_log", []):
        i = idx.get(sy["ts"][:10])
        if i is not None:
            markers.append({"i": i, "color": "#6b4fa0", "label": sy["kind"] + " (patient-logged)"})
    return markers[:20]


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def features_to_patient(features: dict, patient_id: str, name: str = "Live upload") -> dict:
    """Deterministic brief content from parsed features (LLM narrative comes later)."""
    f = features
    watch = f["has_watch_data"]
    ev = f["events"]
    negatives, headline_bits, context = [], [], []

    if watch:
        days = f["rhr"]["days_covered"] or f["days_with_data"]
        if ev["irregular_rhythm"] == 0:
            negatives.append(f"No irregular-rhythm notifications in {days} days of heart-rate coverage")
        if f["ecgs"]:
            cls = {e["classification"] for e in f["ecgs"]}
            negatives.append(f"{f['ecg_count']} ECG recording(s); latest classifications: {', '.join(sorted(cls))}")
        elif f["ecg_count"] == 0:
            negatives.append("No ECG recordings exist in this dataset — rhythm assessed from passive notifications only")
        if ev["high_hr"] == 0 and ev["low_hr"] == 0:
            negatives.append("No high- or low-heart-rate notifications")
        if ev["sleep_apnea"] == 0:
            negatives.append("No sleep-apnoea signals")
        headline_bits.append(
            f"{days} days of cardiac monitoring; resting HR baseline {f['rhr']['baseline_90d']} bpm."
            if f["rhr"]["baseline_90d"] else "Heart-rate data present but sparse.")
        state = "green"
        if ev["irregular_rhythm"] > 0:
            headline_bits.append(f"{ev['irregular_rhythm']} irregular-rhythm notification(s) — review recommended.")
            state = "amber"
    else:
        headline_bits.append(
            "iPhone-only dataset: no cardiac sensor data present. "
            "Cardiac surveillance is not possible from this feed — a watch or other HR sensor is required.")
        negatives.append("No heart-rate, ECG, or rhythm-notification data in this export")
        state = "insufficient"

    # context lines (tier-2 signals, deviation-framed)
    if f.get("hr_recovery_avg"):
        context.append(f"Heart-rate recovery averages {f['hr_recovery_avg']:.0f} bpm at 1 min post-exercise "
                       f"({'normal (>18)' if f['hr_recovery_avg'] > 18 else 'reduced (<18) — context for functional capacity'})")
    if f.get("vo2max"):
        v = f["vo2max"]
        context.append(f"Estimated VO2max {v['latest']} mL/kg/min, {v['direction']} "
                       f"({v['early']} → {v['late']} across the recorded period)")
    if f.get("sleep"):
        s = f["sleep"]
        basis_note = " (time in bed, phone-estimated — staging unavailable)" if s.get("basis") == "in_bed" else ""
        context.append(f"Sleep averaging {s['avg_h']} h/night over {s['nights']} nights (±{s['sd_h']} h){basis_note}")
    if f.get("respiratory"):
        r = f["respiratory"]
        if abs(r["deviation"]) >= 1.5:
            context.append(f"Respiratory rate {r['recent_7d']}/min over the last 7 nights, "
                           f"{'+' if r['deviation'] > 0 else ''}{r['deviation']}/min vs baseline — possible intercurrent illness")
        else:
            context.append(f"Respiratory rate stable ({r['recent_7d']}/min, baseline {r['baseline']})")
    wl = f.get("weight_last")
    if wl and len((f.get("weight_series") or {}).get("values", [])) < 4:
        age_days = (dt.date.fromisoformat(f["date_range"]["end"]) - dt.date.fromisoformat(wl["date"])).days if f["date_range"].get("end") else 0
        stale = f" — recorded {wl['date']}, {age_days} days old; no scale connected" if age_days > 90 else ""
        context.append(f"Last recorded weight {wl['value']} kg{stale}")
    if f["steps"]["daily_avg_60d"] is not None:
        context.append(f"Activity ~{f['steps']['daily_avg_60d']:,} steps/day over the last 60 recorded days")

    return {
        "id": patient_id,
        "name": name,
        "generated": True,
        "state": state,
        "program": "Live data ingest (demo)",
        "monitoring": {"days_monitored": f["days_with_data"], "date_range": f["date_range"],
                       "sources": ", ".join(f["sources"][:3]) or "unknown",
                       "record_total": f["record_total"]},
        "headline": " ".join(headline_bits),
        "negatives": negatives,
        "context": context,
        "events": ev,
        "event_log": f.get("event_log", []),
        "symptom_log": f.get("symptom_log", []),
        "ecg_count": f["ecg_count"],
        "ecgs": f.get("ecgs", []),
        "rhr_series": {"values": f["rhr"]["series_60d"], "dates": f["rhr"].get("dates_60d", []),
                       "baseline": f["rhr"]["baseline_90d"], "unit": "bpm"},
        "rhr_markers": _rhr_markers(f),
        "vo2_series": f.get("vo2_series", {}),
        "sleep_series": f.get("sleep_series", {}),
        "resp_series": f.get("resp_series", {}),
        "walkhr_series": f.get("walkhr_series", {}),
        "sixmin_series": f.get("sixmin_series", {}),
        "extime_weekly": f.get("extime_weekly", {}),
        "afib_series": f.get("afib_series", {}),
        "wrist_series": f.get("wrist_series", {}),
        "steady_series": f.get("steady_series", {}),
        "spo2_series": f.get("spo2_series", {}),
        "fat_series": f.get("fat_series", {}),
        "weight_series": f.get("weight_series", {}),
        "bp_series": f.get("bp_series", {}),
        "steps_series": f["steps"]["series_60d"],
        "steps_dates": f["steps"].get("dates_60d", []),
        "provenance": {
            "cleared": ("ECG classifications, irregular-rhythm / high-low HR / sleep-apnoea notifications (FDA-cleared, Apple)"
                        if watch else "None — no cleared cardiac signals in dataset"),
            "estimates": "Resting HR (derived), VO2max, sleep duration, respiratory rate, step counts",
            "not_assessed": "Ischaemia, structural disease, blood pressure, QT interval"
            + ("" if watch else "; rhythm, rate — no sensor present"),
        },
    }

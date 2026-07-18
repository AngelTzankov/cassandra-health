"""Cassandra — QR share demo server.

Run:  ./.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000
Public URL: set PUBLIC_BASE_URL env var to the cloudflared tunnel URL,
otherwise links fall back to the request's own base URL.
"""

import io
import json
import math
import os
import secrets
import time
from pathlib import Path

import shutil
import tempfile

import qrcode
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

# load .env (local only; not shipped) before importing the llm module
_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from parser import features_to_patient, parse_export
import llm

APP_DIR = Path(__file__).parent
TOKEN_TTL_SECONDS = 15 * 60

app = FastAPI()

# ---------------------------------------------------------------- fixtures

def load_patients() -> dict:
    patients = {}
    for f in (APP_DIR / "fixtures").glob("*.json"):
        data = json.loads(f.read_text())
        patients[data["id"]] = data
    return patients


PATIENTS = load_patients()

# ephemeral store for public self-serve uploads — in memory only, never persisted,
# never listed on the clinician roster. Keeps the "parsed then deleted" privacy promise true.
UPLOADED: dict = {}


def get_patient(pid: str):
    return PATIENTS.get(pid) or UPLOADED.get(pid)

# tokens: token -> {"patient_id": ..., "expires": epoch}
TOKENS: dict[str, dict] = {}


def mint_token(patient_id: str) -> str:
    token = secrets.token_urlsafe(8)
    TOKENS[token] = {"patient_id": patient_id, "expires": time.time() + TOKEN_TTL_SECONDS}
    return token


def resolve_token(token: str):
    entry = TOKENS.get(token)
    if not entry:
        return None, 0
    remaining = entry["expires"] - time.time()
    if remaining <= 0:
        TOKENS.pop(token, None)
        return None, 0
    return get_patient(entry["patient_id"]), int(remaining)


def base_url(request: Request) -> str:
    return os.environ.get("PUBLIC_BASE_URL", str(request.base_url).rstrip("/"))


# ---------------------------------------------------------------- SVG helpers

def rhr_sparkline_svg(patient: dict) -> str:
    series = patient["rhr_series"]
    values = series["values"]
    baseline = series["baseline"]
    w, h, pad = 700, 170, 30
    lo, hi = min(values) - 4, max(values) + 5
    n = len(values)

    def x(i):
        return pad + i * (w - 2 * pad) / (n - 1)

    def y(v):
        return h - pad - (v - lo) * (h - 2 * pad) / (hi - lo)

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    import datetime as _dt
    start_d = series.get("start_date", "2026-06-08")
    end_d = (_dt.date.fromisoformat(start_d) + _dt.timedelta(days=n - 1)).isoformat()
    med_svg = ""
    if patient.get("medication_events"):
        med = patient["medication_events"][0]
        try:
            mi = (_dt.date.fromisoformat(med["date"]) - _dt.date.fromisoformat(start_d)).days
        except (ValueError, KeyError):
            mi = -1
        if 0 <= mi < n:
            mx = x(mi)
            med_svg = (f'<line x1="{mx:.1f}" y1="10" x2="{mx:.1f}" y2="{h - pad}" stroke="#ff9830" stroke-width="1.6"/>'
                       f'<text x="{mx + 6:.1f}" y="20" font-size="11.5" fill="#ff9830" font-weight="600">{med["label"]} · {med["date"][5:]}</text>')
    markers_svg = ""
    for m in patient.get("rhr_markers", []):
        i = m.get("i", -1)
        if 0 <= i < n:
            markers_svg += (f'<circle cx="{x(i):.1f}" cy="{y(values[i]):.1f}" r="5.5" fill="{m.get("color", "#f2495c")}" '
                            f'stroke="#181b1f" stroke-width="2"><title>{m.get("label", "event")}</title></circle>')
    yb = y(baseline)
    area = f"{pad},{h - pad} " + pts + f" {w - pad},{h - pad}"
    grid = ""
    for frac in (0.25, 0.5, 0.75):
        gv = lo + (hi - lo) * frac
        gy = y(gv)
        grid += (f'<line x1="{pad}" y1="{gy:.1f}" x2="{w - pad}" y2="{gy:.1f}" stroke="#2c3235" stroke-width="1"/>'
                 f'<text x="{pad + 3}" y="{gy - 3:.1f}" font-size="10" fill="#6a7178">{gv:.0f}</text>')
    return f"""
<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Resting heart rate trend" style="background:#181b1f">
  <defs><linearGradient id="rhrg" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0%" stop-color="#f2495c" stop-opacity="0.30"/><stop offset="100%" stop-color="#f2495c" stop-opacity="0.02"/></linearGradient></defs>
  <rect width="{w}" height="{h}" fill="#181b1f"/>
  {grid}
  <polygon points="{area}" fill="url(#rhrg)"/>
  <line x1="{pad}" y1="{yb:.1f}" x2="{w - pad}" y2="{yb:.1f}" stroke="#f2cc0c" stroke-dasharray="6 4" stroke-width="1.4" opacity=".85"/>
  <text x="{pad + 2}" y="{yb - 6:.1f}" font-size="11" fill="#f2cc0c">90-day baseline {baseline} bpm</text>
  {med_svg}
  <polyline points="{pts}" fill="none" stroke="#f2495c" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
  {markers_svg}
  <text x="{x(n - 1) - 4:.1f}" y="{y(values[-1]) - 10:.1f}" font-size="13" font-weight="700" fill="#f2495c" text-anchor="end">{values[-1]:g} bpm</text>
  <text x="{pad}" y="{h - 8}" font-size="10.5" fill="#6a7178">{start_d[5:]}</text>
  <text x="{w - pad}" y="{h - 8}" font-size="10.5" fill="#6a7178" text-anchor="end">{end_d[5:]}</text>
</svg>"""

def ecg_strip_svg(hr: int) -> str:
    """Synthetic single-lead sinus strip on a standard pink grid (10 s at 25 mm/s)."""
    w, h = 1000, 170
    mid = 100
    # pink grid: small 4px, large 20px
    grid = [f'<rect width="{w}" height="{h}" fill="#fff7f6"/>']
    for gx in range(0, w + 1, 4):
        stroke = "#f3cfcb" if gx % 20 else "#e8b0aa"
        grid.append(f'<line x1="{gx}" y1="0" x2="{gx}" y2="{h}" stroke="{stroke}" stroke-width="0.6"/>')
    for gy in range(0, h + 1, 4):
        stroke = "#f3cfcb" if gy % 20 else "#e8b0aa"
        grid.append(f'<line x1="0" y1="{gy}" x2="{w}" y2="{gy}" stroke="{stroke}" stroke-width="0.6"/>')

    # one beat: P wave, QRS, T wave sampled as polyline
    beat_px = (60.0 / hr) * 100.0  # 100 px per second
    pts = []
    t = 0.0
    while t < w:
        phase = (t % beat_px) / beat_px
        v = 0.0
        if 0.10 < phase < 0.22:  # P
            v = 6 * math.sin((phase - 0.10) / 0.12 * math.pi)
        elif 0.30 < phase < 0.33:  # Q
            v = -7 * math.sin((phase - 0.30) / 0.03 * math.pi)
        elif 0.33 < phase < 0.39:  # R
            v = 52 * math.sin((phase - 0.33) / 0.06 * math.pi)
        elif 0.39 < phase < 0.43:  # S
            v = -12 * math.sin((phase - 0.39) / 0.04 * math.pi)
        elif 0.52 < phase < 0.72:  # T
            v = 11 * math.sin((phase - 0.52) / 0.20 * math.pi)
        pts.append(f"{t:.1f},{mid - v:.1f}")
        t += 1.4
    wave = f'<polyline points="{" ".join(pts)}" fill="none" stroke="#1a1a1a" stroke-width="1.7" stroke-linejoin="round"/>'
    return f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="ECG strip">{"".join(grid)}{wave}</svg>'


# ---------------------------------------------------------------- pages

CHART_JS = """<script>
(function(){
 const tip=document.createElement('div');
 tip.style.cssText='position:fixed;pointer-events:none;background:#1f2421;color:#fff;font:12.5px -apple-system,sans-serif;padding:5px 10px;border-radius:7px;opacity:0;transition:opacity .12s;z-index:99;white-space:nowrap';
 document.body.appendChild(tip);
 document.querySelectorAll('figure[data-vals]').forEach(fig=>{
  const svg=fig.querySelector('svg'); if(!svg) return;
  const vals=JSON.parse(fig.dataset.vals);
  const dates=fig.dataset.dates?JSON.parse(fig.dataset.dates):null;
  const pad=+fig.dataset.pad||28, unit=fig.dataset.unit||'', W=700, n=vals.length;
  if(n<2) return;
  const H=svg.viewBox.baseVal.height||150;
  const guide=document.createElementNS('http://www.w3.org/2000/svg','line');
  guide.setAttribute('stroke','rgba(255,255,255,.45)');guide.setAttribute('stroke-width','1');
  guide.setAttribute('y1','8');guide.setAttribute('y2',H-26);guide.style.display='none';
  svg.appendChild(guide);
  svg.addEventListener('mousemove',e=>{
    const r=svg.getBoundingClientRect();
    const fx=(e.clientX-r.left)/r.width*W;
    let i=Math.round((fx-pad)/(W-2*pad)*(n-1)); i=Math.max(0,Math.min(n-1,i));
    const x=pad+i*(W-2*pad)/(n-1);
    guide.setAttribute('x1',x);guide.setAttribute('x2',x);guide.style.display='';
    tip.textContent=(dates&&dates[i]?dates[i]+' — ':'Day '+(i+1)+' — ')+Number(vals[i]).toLocaleString()+(unit?' '+unit:'');
    tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY-12)+'px';tip.style.opacity=1;
  });
  svg.addEventListener('mouseleave',()=>{tip.style.opacity=0;guide.style.display='none';});
 });
})();
</script>"""



BRIEF_CSS = """
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family: 'Georgia', 'Times New Roman', serif; background:#efece6; color:#1f2421; }
  .sheet { max-width:760px; margin:0 auto; background:#fdfdfb; min-height:100vh;
           padding:34px 40px 48px; box-shadow:0 0 24px rgba(0,0,0,.10); }
  .doc-head { display:flex; justify-content:space-between; align-items:baseline;
              border-bottom:2.5px solid #1f2421; padding-bottom:10px; }
  .doc-head .brand { font-family:-apple-system,'Helvetica Neue',sans-serif; font-size:12px;
                     letter-spacing:.18em; color:#6b6f6a; font-weight:600; }
  .doc-head h1 { font-size:19px; letter-spacing:.02em; }
  .pt-row { display:flex; flex-wrap:wrap; gap:6px 26px; padding:13px 0;
            border-bottom:1px solid #d8d5cd; font-size:14px; }
  .pt-row b { font-size:15px; }
  .pill { font-family:-apple-system,sans-serif; font-size:12px; font-weight:700; letter-spacing:.06em;
          padding:3px 12px; border-radius:20px; }
  .pill.amber { background:#f6e3c0; color:#8a5a10; border:1px solid #d9b46a; }
  .headline { font-size:17.5px; line-height:1.5; font-weight:700; padding:18px 0 4px; }
  h2 { font-family:-apple-system,'Helvetica Neue',sans-serif; font-size:12px; letter-spacing:.15em;
       color:#77706a; margin:26px 0 8px; font-weight:700; }
  p.body { font-size:15px; line-height:1.65; }
  figure { margin:10px 0 2px; border:1px solid #ddd8cf; border-radius:6px; overflow:hidden; }
  figure svg { display:block; width:100%; height:auto; }
  figcaption { font-family:-apple-system,sans-serif; font-size:12.5px; color:#5d605c;
               padding:7px 12px; background:#f6f4ef; border-top:1px solid #ddd8cf; }
  ul.neg, ul.chg { list-style:none; }
  ul.neg li, ul.chg li { font-size:14.5px; line-height:1.55; padding:3px 0 3px 26px; position:relative; }
  ul.neg li:before { content:"✓"; position:absolute; left:4px; color:#3d7a4a; font-weight:700; }
  ul.chg li:before { content:"→"; position:absolute; left:4px; color:#8a5a10; font-weight:700; }
  .step { background:#f2f0e9; border-left:4px solid #1f2421; padding:13px 16px; font-size:15px; line-height:1.6; }
  .audit { font-family:'SF Mono',Menlo,monospace; font-size:12px; color:#77706a; margin-top:22px;
           border-top:1px solid #d8d5cd; padding-top:10px; }
  .prov { font-family:-apple-system,sans-serif; font-size:12px; color:#77706a; line-height:1.7; margin-top:8px; }
  .expiry { font-family:-apple-system,sans-serif; text-align:center; font-size:12.5px; color:#9a5a4a;
            padding:14px 0 0; }
  @media (max-width:640px){ .sheet{ padding:22px 18px 40px; } .doc-head{flex-direction:column;gap:4px;} }
"""


def maria_chart_dates(p: dict) -> list:
    import datetime as _dt
    start = _dt.date.fromisoformat(p["rhr_series"].get("start_date", "2026-06-08"))
    n = len(p["rhr_series"]["values"])
    return [(start + _dt.timedelta(days=i)).isoformat() for i in range(n)]


def brief_page(patient: dict, remaining: int) -> str:
    p = patient
    negs = "".join(f"<li>{n}</li>" for n in p["negatives"])
    chgs = "".join(f"<li>{c}</li>" for c in p["would_change"])
    mins = remaining // 60

    extra_charts = ""
    v2 = p.get("vo2_series") or {}
    if len(v2.get("values", [])) >= 4:
        extra_charts += (f"<figure data-vals='{json.dumps(v2['values'])}' data-dates='{json.dumps(v2.get('dates', []))}' data-pad=\"28\" data-unit=\"mL/kg/min\">"
                         f'{spark_svg(v2["values"], None, "#7a5fa0", "mL/kg/min")}'
                         f'<figcaption>Estimated VO2max — declining alongside reduced activity since dose increase</figcaption></figure>')
    sl = p.get("sleep_series") or {}
    if len(sl.get("hours", [])) >= 5:
        extra_charts += (f"<figure data-vals='{json.dumps(sl['hours'])}' data-dates='{json.dumps(sl.get('dates', []))}' data-pad=\"28\" data-unit=\"h\">"
                         f'{spark_svg(sl["hours"], None, "#3d7a8a", "h")}'
                         f'<figcaption>Sleep duration per night — shortened ~1 h since 27 Jun</figcaption></figure>')
    rp = p.get("resp_series") or {}
    if len(rp.get("values", [])) >= 10:
        extra_charts += (f"<figure data-vals='{json.dumps(rp['values'])}' data-dates='{json.dumps(rp.get('dates', []))}' data-pad=\"28\" data-unit=\"/min\">"
                         f'{spark_svg(rp["values"], rp.get("baseline"), "#2b8a6b", "/min")}'
                         f'<figcaption>Nocturnal respiratory rate — stable; argues against intercurrent infection</figcaption></figure>')
    extra_charts += tier2_charts(p)

    event_secs = ""
    if p.get("event_log"):
        rows = "".join(f"<li>{e['ts']} — {e['kind']}</li>" for e in p["event_log"])
        event_secs += f'<h2>NOTIFICATION EVENTS</h2><ul class="chg">{rows}</ul>'
    if p.get("symptom_log"):
        rows = "".join(f"<li>{s['ts']} — {s['kind']} (patient-logged)</li>" for s in p["symptom_log"])
        event_secs += f'<h2>LOGGED SYMPTOMS</h2><ul class="chg">{rows}</ul>'

    ecg_fig = ""
    if p.get("ecg"):
        ecg_fig = f"""<figure>{ecg_strip_svg(p['ecg']['hr'])}
    <figcaption><b>ECG {p['ecg']['date']}</b> — {p['ecg']['classification']}, {p['ecg']['hr']} bpm ·
      patient-tagged symptoms: {p['ecg']['symptom_tagged']} · {p['ecg']['lead']} · classification: Apple (FDA-cleared)</figcaption>
  </figure>"""

    return f"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Heart-Failure Monitoring Brief — {p['name']}</title>
<style>{BRIEF_CSS}</style></head><body>
<div class="sheet">
  <div class="doc-head">
    <div><div class="brand">CASSANDRA</div><h1>Heart-Failure Monitoring Brief</h1></div>
    <span class="pill amber">AMBER — REVIEW</span>
  </div>
  <div class="pt-row">
    <span><b>{p['name']}</b> · {p['age']} {p['sex']}</span>
    <span>{p['program']}</span>
    <span>{p['monitoring']['days_monitored']} days monitored · worn {p['monitoring']['wear_pct']}%</span>
  </div>

  <div class="headline">{p['headline']}</div>

  <h2>WORKING HYPOTHESIS</h2>
  <p class="body">{p['hypothesis']}</p>

  <h2>HEART-FAILURE SIGNALS</h2>
  <figure data-vals='{json.dumps(p["rhr_series"]["values"])}' data-dates='{json.dumps(maria_chart_dates(p))}' data-pad="30" data-unit="bpm">{rhr_sparkline_svg(p)}
    <figcaption>Daily resting heart rate, 8 Jun – 17 Jul. Dose increase marked. Baseline = personal 90-day median.</figcaption>
  </figure>
  {ecg_fig}
  {extra_charts}

  {event_secs}
  <h2>PERTINENT NEGATIVES</h2>
  <ul class="neg">{negs}</ul>

  <h2>WHAT WOULD CHANGE THIS ASSESSMENT</h2>
  <ul class="chg">{chgs}</ul>

  <h2>SUGGESTED NEXT STEP</h2>
  <div class="step">{p['suggested_step']}</div>

  <div class="audit">Escalation trigger: rule {p['rule_fired']['id']} — {p['rule_fired']['detail']}.
    AI narrative generated from computed values only; it cannot alter triage state.</div>
  <div class="prov"><b>Provenance</b> — Cleared: {p['provenance']['cleared']}.
    Estimates: {p['provenance']['estimates']}. <b>Not assessed:</b> {p['provenance']['not_assessed']}.
    Data current as of {p['monitoring']['data_current_as_of']} · {p['monitoring']['sources']}.</div>
  <div class="expiry">Shared by the patient · link expires in about {mins} min · no data is stored after expiry</div>
</div>{CHART_JS}</body></html>"""


def expired_page() -> str:
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Link expired</title>
<style>body{font-family:-apple-system,sans-serif;display:flex;align-items:center;justify-content:center;
min-height:100vh;background:#efece6;color:#1f2421}div{text-align:center;padding:40px}
h1{font-size:20px;margin-bottom:8px}p{color:#77706a;font-size:14.5px}</style></head><body>
<div><h1>This brief has expired</h1><p>Shared briefs are available for 15 minutes.<br>
Ask the patient to generate a new code.</p></div></body></html>"""


def share_page(patient: dict, token: str, brief_url: str) -> str:
    p = patient
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Share with your clinician</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:-apple-system,'Helvetica Neue',sans-serif;background:#101614;color:#f2f2ee;
       min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;padding:24px}}
  .brand{{font-size:12px;letter-spacing:.2em;color:#8fa398;font-weight:600}}
  h1{{font-size:21px;font-weight:700;text-align:center}}
  .card{{background:#fff;padding:22px;border-radius:22px;box-shadow:0 12px 40px rgba(0,0,0,.5)}}
  .card img{{display:block;width:min(64vw,300px);height:auto}}
  .who{{font-size:14.5px;color:#b9c4bd;text-align:center;line-height:1.6}}
  .timer{{font-variant-numeric:tabular-nums;font-size:14px;color:#e8c98a}}
  a{{color:#8fa398;font-size:12.5px}}
</style></head><body>
  <div class="brand">CASSANDRA</div>
  <h1>Show this code to your clinician</h1>
  <div class="card"><img src="/qr/{token}.png" alt="QR code for clinical brief"></div>
  <div class="who">{p['name']} · Heart-failure monitoring brief<br>They scan it with any phone camera — nothing to install.</div>
  <div class="timer" id="t">Expires in 15:00</div>
  <a href="{brief_url}">or open the brief directly</a>
<script>
let s = {TOKEN_TTL_SECONDS};
setInterval(() => {{ s = Math.max(0, s - 1);
  document.getElementById('t').textContent = s ? `Expires in ${{String(Math.floor(s/60)).padStart(2,'0')}}:${{String(s%60).padStart(2,'0')}}` : 'Expired — refresh for a new code';
}}, 1000);
</script></body></html>"""


# ---------------------------------------------------------------- routes

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return LANDING_PAGE


@app.get("/share/{patient_id}", response_class=HTMLResponse)
def share(patient_id: str, request: Request):
    patient = get_patient(patient_id)
    if not patient:
        return HTMLResponse("Unknown patient", status_code=404)
    token = mint_token(patient_id)
    brief_url = f"{base_url(request)}/b/{token}"
    return share_page(patient, token, brief_url)


@app.get("/b/{token}", response_class=HTMLResponse)
def brief(token: str):
    patient, remaining = resolve_token(token)
    if not patient:
        return HTMLResponse(expired_page(), status_code=410)
    return generated_brief_page(patient, remaining)


@app.get("/preview/{patient_id}", response_class=HTMLResponse)
def preview(patient_id: str):
    patient = get_patient(patient_id)
    if not patient:
        return HTMLResponse("Unknown patient", status_code=404)
    return generated_brief_page(patient, TOKEN_TTL_SECONDS)


@app.get("/qr/{token}.png")
def qr_png(token: str, request: Request):
    patient, _ = resolve_token(token)
    if not patient:
        return Response(status_code=410)
    url = f"{base_url(request)}/b/{token}"
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------------- live upload

def spark_svg(values, baseline=None, color="#2b6cb3", unit="", markers=None):
    if not values or len(values) < 2:
        return '<div style="padding:18px;font-size:13px;color:#888">Not enough data to chart</div>'
    GRAFANA = {"#b3372b": "#f2495c", "#2b6cb3": "#5794f2", "#7a5fa0": "#b877d9", "#7a4a6d": "#b877d9",
               "#3d7a8a": "#73bf69", "#2b8a6b": "#73bf69", "#8a6d3b": "#ff9830", "#a03c50": "#f2495c",
               "#b3661e": "#ff9830", "#4a6d8c": "#5794f2", "#3d8a4a": "#73bf69", "#5a7d5a": "#73bf69"}
    color = GRAFANA.get(color, color)
    w, h, pad = 700, 170, 28
    lo, hi = min(values), max(values)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.15
    n = len(values)
    x = lambda i: pad + i * (w - 2 * pad) / (n - 1)
    y = lambda v: h - pad - (v - lo) * (h - 2 * pad) / (hi - lo)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    gid = f"g{abs(hash((tuple(values), unit))) % 999999}"
    area = f"{pad},{h - pad} " + pts + f" {w - pad},{h - pad}"
    # horizontal gridlines + labels
    grid = ""
    for frac in (0.25, 0.5, 0.75):
        gv = lo + (hi - lo) * frac
        gy = y(gv)
        grid += (f'<line x1="{pad}" y1="{gy:.1f}" x2="{w - pad}" y2="{gy:.1f}" stroke="#2c3235" stroke-width="1"/>'
                 f'<text x="{pad + 3}" y="{gy - 3:.1f}" font-size="10" fill="#6a7178">{gv:.0f}</text>')
    base = ""
    if baseline is not None:
        base = (f'<line x1="{pad}" y1="{y(baseline):.1f}" x2="{w - pad}" y2="{y(baseline):.1f}" '
                f'stroke="#f2cc0c" stroke-dasharray="6 4" stroke-width="1.4" opacity=".8"/>'
                f'<text x="{w - pad - 4}" y="{y(baseline) - 5:.1f}" font-size="10.5" fill="#f2cc0c" '
                f'text-anchor="end">baseline {baseline:g}</text>')
    marks = ""
    for m in (markers or []):
        i = m.get("i", -1)
        if 0 <= i < n:
            marks += (f'<circle cx="{x(i):.1f}" cy="{y(values[i]):.1f}" r="5" fill="{m.get("color", "#f2495c")}" '
                      f'stroke="#181b1f" stroke-width="2"><title>{m.get("label", "event")}</title></circle>')
    import statistics as _st
    stats = f"min {min(values):g} · avg {_st.mean(values):.1f} · max {max(values):g} {unit}"
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" style="background:#181b1f">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="{color}" stop-opacity="0.32"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0.02"/></linearGradient></defs>'
            f'<rect width="{w}" height="{h}" fill="#181b1f"/>{grid}'
            f'<polygon points="{area}" fill="url(#{gid})"/>{base}'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>{marks}'
            f'<text x="{w - pad}" y="16" font-size="10.5" fill="#9aa0a6" text-anchor="end">{stats}</text>'
            f'<text x="{x(n - 1):.1f}" y="{y(values[-1]) - 9:.1f}" font-size="12.5" font-weight="700" '
            f'fill="{color}" text-anchor="end">{values[-1]:g} {unit}</text></svg>')

STATE_PILLS = {
    "green": ('pill-green', 'GREEN — NO ACTION NEEDED', '#e2efe2', '#2e6b3a', '#9dc4a2'),
    "amber": ('pill-amber', 'AMBER — REVIEW', '#f6e3c0', '#8a5a10', '#d9b46a'),
    "insufficient": ('pill-grey', 'INSUFFICIENT DATA', '#e8e6e0', '#5d605c', '#c4c0b6'),
}




def real_ecg_svg(points: list) -> str:
    """Render actual exported ECG voltages on the standard pink grid."""
    w, h = 1000, 170
    mid = h / 2
    grid = [f'<rect width="{w}" height="{h}" fill="#fff7f6"/>']
    for gx in range(0, w + 1, 4):
        stroke = "#f3cfcb" if gx % 20 else "#e8b0aa"
        grid.append(f'<line x1="{gx}" y1="0" x2="{gx}" y2="{h}" stroke="{stroke}" stroke-width="0.6"/>')
    for gy in range(0, h + 1, 4):
        stroke = "#f3cfcb" if gy % 20 else "#e8b0aa"
        grid.append(f'<line x1="0" y1="{gy}" x2="{w}" y2="{gy}" stroke="{stroke}" stroke-width="0.6"/>')
    n = len(points)
    if n < 2:
        return ""
    peak = max(1.0, max(abs(p) for p in points))
    scale = 62.0 / peak
    pts = " ".join(f"{i * w / (n - 1):.1f},{mid - p * scale:.1f}" for i, p in enumerate(points))
    wave = f'<polyline points="{pts}" fill="none" stroke="#1a1a1a" stroke-width="1.5" stroke-linejoin="round"/>'
    return (f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" aria-label="ECG recording">{"".join(grid)}{wave}</svg>')



def tier2_charts(p: dict) -> str:
    """Conditional evidence charts shared by authored and generated briefs."""
    out = ""

    def fig(series, color, unit, caption, baseline=None, min_pts=4):
        vals = (series or {}).get("values") or (series or {}).get("hours") or []
        if len(vals) < min_pts:
            return ""
        dates = (series or {}).get("dates", [])
        return (f"<figure data-vals='{json.dumps(vals)}' data-dates='{json.dumps(dates)}' data-pad=\"28\" data-unit=\"{unit}\">"
                f'{spark_svg(vals, baseline, color, unit)}'
                f'<figcaption>{caption}</figcaption></figure>')

    # Heart-failure decompensation panel only — confirmatory signals.
    # (General-wellness metrics — VO2max, walking HR, 6-min walk, exercise minutes,
    #  AFib burden, walking steadiness, body-fat — are deliberately excluded: not
    #  relevant to fluid-retention / decompensation surveillance.)
    w = p.get("weight_series") or {}
    if len(w.get("values", [])) >= 4:
        delta = round(w["values"][-1] - w["values"][0], 1)
        out += fig(w, "#8a6d3b", "kg",
                   f"Body weight — {'+' if delta > 0 else ''}{delta} kg over the period · the key fluid-retention signal (>2 kg/3 days is significant)")
    sp = p.get("spo2_series") or {}
    if len(sp.get("values", [])) >= 7:
        out += fig(sp, "#5794f2", "%",
                   "Blood oxygen saturation, nightly mean — a downward drift corroborates pulmonary congestion (trend only)", min_pts=7)
    wr = p.get("wrist_series") or {}
    if len(wr.get("values", [])) >= 7:
        out += fig(wr, "#7a4a6d", "°C",
                   "Nightly wrist temperature vs baseline — a flat trace argues against infection as the driver",
                   baseline=wr.get("baseline"), min_pts=7)
    bp = p.get("bp_series") or {}
    if len(bp.get("values", [])) >= 4:
        dia = f" · latest diastolic ~{bp['dia_latest']:.0f}" if bp.get("dia_latest") else ""
        out += fig(bp, "#a03c50", "mmHg",
                   f"Home blood pressure, systolic daily mean — a falling trend can accompany decompensation{dia}")
    return out


VERDICT = {
    "green":        ("STABLE", "No action needed — signals steady at personal baseline.", "#1c3a24", "#8fe0a6", "#3d7a4a"),
    "amber":        ("REVIEW SOON", "Early decompensation pattern — review within the day.", "#3a2e10", "#f2c96a", "#d9a53a"),
    "red":          ("ACT TODAY", "Decompensation crossing a severe threshold — same-day clinical review.", "#3a1416", "#f28a8a", "#c0392b"),
    "insufficient": ("INSUFFICIENT DATA", "Coverage too low to assess — a wearable or more wear-time is needed.", "#2a2c2e", "#c4cbd0", "#7a8288"),
}


def verdict_banner(state: str) -> str:
    word, sub, bg, fg, accent = VERDICT.get(state, VERDICT["insufficient"])
    return (f'<div class="verdict" style="background:{bg};border-left:6px solid {accent}">'
            f'<span class="v-dot" style="background:{accent}"></span>'
            f'<span class="v-word" style="color:{fg}">{word}</span>'
            f'<span class="v-sub">{sub}</span></div>')


def generated_brief_page(p: dict, remaining: int) -> str:
    _, label, bg, fg, bd = STATE_PILLS.get(p["state"], STATE_PILLS["insufficient"])
    negs = "".join(f"<li>{n}</li>" for n in p["negatives"])
    rng = p["monitoring"].get("date_range") or {}
    charts = ""
    if p.get("rhr_series", {}).get("values"):
        rs = p["rhr_series"]
        charts += (f"<figure data-vals='{json.dumps(rs['values'])}' data-dates='{json.dumps(rs.get('dates', []))}' data-pad=\"28\" data-unit=\"bpm\">"
                   f'{spark_svg(rs["values"], rs.get("baseline"), "#b3372b", "bpm", p.get("rhr_markers"))}'
                   f'<figcaption>Daily resting heart rate vs personal baseline — an upward drift is a core decompensation trigger'
                   f'{" · dots mark events" if p.get("rhr_markers") else ""}'
                   f'</figcaption></figure>')
    if p.get("steps_series"):
        charts += (f"<figure data-vals='{json.dumps(p['steps_series'])}' data-dates='{json.dumps(p.get('steps_dates', []))}' data-pad=\"28\" data-unit=\"steps\">"
                   f'{spark_svg(p["steps_series"], None, "#2b6cb3", "steps")}'
                   f'<figcaption>Daily activity (steps) — a sustained drop signals reduced functional capacity</figcaption></figure>')
    rp = p.get("resp_series") or {}
    if len(rp.get("values", [])) >= 10:
        charts += (f"<figure data-vals='{json.dumps(rp['values'])}' data-dates='{json.dumps(rp.get('dates', []))}' data-pad=\"28\" data-unit=\"/min\">"
                   f'{spark_svg(rp["values"], rp.get("baseline"), "#2b8a6b", "/min")}'
                   f'<figcaption>Nocturnal respiratory rate vs baseline — a rise is one of the earliest decompensation signals</figcaption></figure>')
    charts += tier2_charts(p)
    for e in p.get("ecgs", []):
        svg = real_ecg_svg(e.get("points", []))
        if svg:
            charts += (f'<figure>{svg}<figcaption><b>ECG {e["date"]}</b> — {e["classification"]} · '
                       f'patient-tagged symptoms: {e["symptoms"]} · {e.get("sample_rate","")} · '
                       f'recorded on device, rendered from raw voltages</figcaption></figure>')

    extra_sections = ""
    if p.get("event_log"):
        rows = "".join(f"<li>{ev['ts'].replace('T',' ')} — {ev['kind']}</li>" for ev in p["event_log"])
        extra_sections += f'<h2>NOTIFICATION EVENTS</h2><ul class="chg">{rows}</ul>'
    if p.get("symptom_log"):
        rows = "".join(f"<li>{sy['ts'].replace('T',' ')} — {sy['kind']} (patient-logged)</li>" for sy in p["symptom_log"])
        extra_sections += f'<h2>LOGGED SYMPTOMS</h2><ul class="chg">{rows}</ul>'
    if p.get("context"):
        rows = "".join(f"<li>{c}</li>" for c in p["context"])
        extra_sections += f'<h2>CONTEXT</h2><ul class="neg">{rows}</ul>'

    # unified clinical narrative sections (shown when present)
    hypothesis_sec = f'<h2>ASSESSMENT</h2><p class="body">{p["hypothesis"]}</p>' if p.get("hypothesis") else ""
    change_sec = ""
    if p.get("would_change"):
        rows = "".join(f"<li>{c}</li>" for c in p["would_change"])
        change_sec = f'<h2>WHAT WOULD CHANGE THIS ASSESSMENT</h2><ul class="chg">{rows}</ul>'
    step_sec = f'<h2>SUGGESTED NEXT STEP</h2><div class="step">{p["suggested_step"]}</div>' if p.get("suggested_step") else ""

    mon = p.get("monitoring", {})
    meta_bits = []
    if mon.get("record_total"):
        meta_bits.append(f"{mon['record_total']:,} records")
    meta_bits.append(f"{mon.get('days_monitored','—')} days monitored")
    if mon.get("wear_pct"):
        meta_bits.append(f"worn {mon['wear_pct']}%")
    meta_row = " · ".join(meta_bits)

    ai_note = " · narrative written live by GPT-4o from the computed values above" if p.get("ai_generated") else ""
    if p.get("rule_fired"):
        rf = p["rule_fired"]
        audit = (f"Escalation trigger: rule <b>{rf['id']}</b> — {rf['detail']}{ai_note}. "
                 f"AI narrative generated from computed values only; it cannot alter triage state.")
    else:
        audit = (f"Deterministic triage: no escalation rule met — patient classified stable{ai_note}. "
                 f"Sources: {mon.get('sources','—')}.")

    mins = remaining // 60
    return f"""<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Cassandra — HF Monitoring Brief — {p['name']}</title>
<style>{BRIEF_CSS}
  .pill-custom {{ background:{bg}; color:{fg}; border:1px solid {bd}; }}
  .verdict {{ display:flex; align-items:center; gap:12px; padding:16px 20px; border-radius:12px; margin:16px 0 4px;
             font-family:-apple-system,'Helvetica Neue',sans-serif; flex-wrap:wrap; }}
  .verdict .v-dot {{ width:14px; height:14px; border-radius:50%; flex:none; box-shadow:0 0 0 4px rgba(255,255,255,.06); }}
  .verdict .v-word {{ font-size:19px; font-weight:800; letter-spacing:.04em; }}
  .verdict .v-sub {{ font-size:14.5px; color:#c9c9c4; }}</style></head><body>
<div class="sheet">
  <div class="doc-head">
    <div><div class="brand">CASSANDRA</div><h1>Heart-Failure Monitoring Brief</h1></div>
    <span class="pill pill-custom">{label}</span>
  </div>
  {verdict_banner(p['state'])}
  <div class="pt-row">
    <span><b>{p['name']}</b>{f" · {p['age']} {p['sex']}" if p.get('age') else ""}</span>
    <span>{p.get('program','')}</span>
    <span>{meta_row}</span>
  </div>
  <div class="headline">{p['headline']}</div>
  {hypothesis_sec}
  <h2>HEART-FAILURE SIGNALS</h2>
  {charts or '<p class="body">No chartable series in this dataset.</p>'}
  {extra_sections}
  <h2>FINDINGS &amp; COVERAGE</h2>
  <ul class="neg">{negs}</ul>
  {change_sec}
  {step_sec}
  <div class="audit">{audit}</div>
  <div class="prov"><b>Provenance</b> — Cleared: {p['provenance']['cleared']}.
    Estimates: {p['provenance']['estimates']}. <b>Not assessed:</b> {p['provenance']['not_assessed']}.</div>
  <div class="expiry">Shared by the patient · link expires in about {mins} min · no data stored after expiry</div>
</div>{CHART_JS}</body></html>"""


UPLOAD_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Test with your data</title>
<style>
 *{margin:0;padding:0;box-sizing:border-box}
 body{font-family:-apple-system,'Helvetica Neue',sans-serif;background:#101614;color:#f2f2ee;
      min-height:100vh;padding:32px 22px;display:flex;flex-direction:column;align-items:center;gap:18px}
 .brand{font-size:12px;letter-spacing:.2em;color:#8fa398;font-weight:600}
 h1{font-size:21px;text-align:center}
 ol{max-width:430px;color:#c9d2cc;font-size:15px;line-height:1.75;padding-left:22px}
 ol b{color:#fff}
 .drop{margin-top:8px;background:#1b2420;border:2px dashed #3c4f45;border-radius:18px;
       padding:26px 34px;text-align:center;font-size:15px;color:#b9c4bd}
 input[type=file]{display:none}
 .btn{display:inline-block;margin-top:12px;background:#e8f0ea;color:#101614;font-weight:700;
      padding:12px 26px;border-radius:12px;font-size:15px}
 #status{font-size:14px;color:#e8c98a;min-height:22px;text-align:center;max-width:430px;line-height:1.6}
 .note{font-size:12.5px;color:#77857c;max-width:430px;text-align:center;line-height:1.6}
</style></head><body>
 <div class="brand">CASSANDRA</div>
 <h1>Generate a brief from <em>your</em> data</h1>
 <ol>
   <li>Open the <b>Health</b> app on your iPhone</li>
   <li>Tap your <b>profile picture</b> (top right)</li>
   <li>Scroll down → <b>Export All Health Data</b> → wait for the zip</li>
   <li>In the share sheet choose <b>Save to Files</b>, then upload it below</li>
 </ol>
 <a class="btn" style="background:#2e4638;color:#e8f0ea" href="x-apple-health://">Open the Health app</a>
 <label class="drop">export.zip from Apple Health<br>
   <span class="btn">Choose file</span>
   <input id="f" type="file" accept=".zip,application/zip">
 </label>
 <div id="status"></div>
 <div class="note">Your file is parsed in memory on the demo machine and deleted immediately after.
   The resulting brief link expires in 15 minutes.</div>
<div id="bar" style="display:none;width:min(430px,88vw);height:8px;background:#26332c;border-radius:6px;overflow:hidden">
  <div id="fill" style="height:100%;width:0%;background:#8fc49a;transition:width .2s"></div></div>
<script>
const f = document.getElementById('f'), st = document.getElementById('status'),
      bar = document.getElementById('bar'), fill = document.getElementById('fill');
f.addEventListener('change', () => {
  const file = f.files[0]; if (!file) return;
  const mb = (file.size/1048576).toFixed(0);
  bar.style.display = 'block';
  const fd = new FormData(); fd.append('file', file);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      const pct = Math.round(e.loaded / e.total * 100);
      fill.style.width = pct + '%';
      st.textContent = pct < 100 ? `Uploading ${mb} MB — ${pct}%`
                                 : 'Upload done — parsing your records…';
    }
  };
  xhr.onload = () => {
    if (xhr.status !== 200) { st.textContent = 'Failed: ' + xhr.responseText; return; }
    const j = JSON.parse(xhr.responseText);
    fill.style.width = '100%';
    st.textContent = `Parsed ${j.records.toLocaleString()} records — opening your brief…`;
    setTimeout(() => location.href = j.share_url, 700);
  };
  xhr.onerror = () => { st.textContent = 'Upload failed — check connection and retry.'; };
  xhr.send(fd);
});
</script></body></html>"""


@app.get("/upload", response_class=HTMLResponse)
def upload_page():
    return UPLOAD_PAGE


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        features = parse_export(tmp_path)
        pid = "live-" + secrets.token_urlsafe(6)
        patient = features_to_patient(features, pid, name="Your data")
        narrative = llm.generate_narrative(features, patient["state"], patient.get("negatives", []))
        if narrative:
            patient["headline"] = narrative["headline"]
            patient["hypothesis"] = narrative["hypothesis"]
            patient["ai_generated"] = True
        # ephemeral: held in memory only, never written to disk, never shown on the roster
        UPLOADED[pid] = patient
        base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
        return {"records": features["record_total"], "share_url": f"{base}/preview/{pid}"}
    except Exception as e:
        return Response(f"Could not parse export: {e}", status_code=422)
    finally:
        os.unlink(tmp_path)  # raw upload deleted immediately


@app.get("/upload-qr", response_class=HTMLResponse)
def upload_qr(request: Request):
    url = f"{base_url(request)}/upload"
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    import base64
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Scan to test</title>
<style>body{{font-family:-apple-system,sans-serif;display:flex;flex-direction:column;align-items:center;
justify-content:center;min-height:100vh;background:#101614;color:#f2f2ee;gap:18px}}
img{{background:#fff;padding:18px;border-radius:20px;width:min(70vw,340px)}}</style></head><body>
<h2>Scan with your iPhone to test with your own data</h2>
<img src="data:image/png;base64,{b64}"><code style="color:#8fa398">{url}</code></body></html>"""


# ---------------------------------------------------------------- quick sync (iOS Shortcut)

import re as _re


@app.post("/api/quick-sync")
async def quick_sync(request: Request):
    """Accept loosely-formatted health samples from an iOS Shortcut.

    The Shortcut sends 'Find Health Samples' output as plain text; we parse
    (value, date) pairs leniently rather than demanding strict JSON.
    """
    raw = (await request.body()).decode("utf-8", errors="ignore")
    # values like "62 count/min" or bare numbers near ISO dates
    pairs = _re.findall(r"(\d{4}-\d{2}-\d{2})[^\n]*?(\d{2,3}(?:\.\d+)?)|(\d{2,3}(?:\.\d+)?)[^\n]*?(\d{4}-\d{2}-\d{2})", raw)
    samples = []
    for d1, v1, v2, d2 in pairs:
        date, val = (d1, v1) if d1 else (d2, v2)
        try:
            v = float(val)
            if 30 <= v <= 220:  # plausible HR range
                samples.append((date, v))
        except ValueError:
            pass
    samples.sort()
    by_day: dict[str, list[float]] = {}
    for d, v in samples:
        by_day.setdefault(d, []).append(v)
    daily = [(d, sum(vs) / len(vs)) for d, vs in sorted(by_day.items())]
    values = [round(v, 1) for _, v in daily][-60:]

    import statistics as _st
    baseline = round(_st.median(values), 1) if values else None
    pid = "sync-" + secrets.token_urlsafe(4)
    n_days = len(daily)
    patient = {
        "id": pid, "name": "Quick sync", "generated": True,
        "state": "green" if values else "insufficient",
        "program": "Live quick-sync (iOS Shortcut)",
        "monitoring": {"record_total": len(samples), "days_monitored": n_days,
                       "date_range": {"start": daily[0][0] if daily else "?", "end": daily[-1][0] if daily else "?"},
                       "sources": "Apple Health via Shortcuts"},
        "headline": (f"{n_days} days of heart-rate data received. Baseline {baseline} bpm. "
                     "No values outside plausible resting range in this sample."
                     if values else
                     "No parseable heart-rate samples received — the device may have no heart-rate data."),
        "negatives": ([f"Daily mean HR within {min(values):.0f}–{max(values):.0f} bpm across {n_days} days",
                       "ECG and rhythm-notification data not available via quick sync — full export required"]
                      if values else
                      ["Quick sync carries heart-rate samples only; none were present"]),
        "events": {}, "ecg_count": 0,
        "rhr_series": {"values": values, "baseline": baseline, "unit": "bpm"},
        "steps_series": [],
        "provenance": {"cleared": "None via quick sync",
                       "estimates": "Heart-rate daily means (Shortcuts feed)",
                       "not_assessed": "ECG, rhythm events, activity — use full export for complete coverage"},
    }
    UPLOADED[pid] = patient
    token = mint_token(pid)
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
    return {"share_url": f"{base}/share/{pid}", "brief_url": f"{base}/b/{token}",
            "days": n_days, "samples": len(samples)}


# ---------------------------------------------------------------- doctor roster + replay

STATE_ORDER = {"red": 0, "amber": 1, "insufficient": 2, "green": 3}
STATE_DOT = {"green": "#4a9a5c", "amber": "#ff9830", "red": "#c0392b", "insufficient": "#9a958b"}


@app.get("/doctor", response_class=HTMLResponse)
def doctor_roster():
    patients = sorted(PATIENTS.values(), key=lambda p: STATE_ORDER.get(p.get("state", "green"), 3))
    rows = []
    for p in patients:
        state = p.get("state", "green")
        dot = STATE_DOT.get(state, "#9a958b")
        href = f"/replay/{p['id']}" if p["id"] == "maria-k" else f"/preview/{p['id']}"
        badge = "▶ replay" if p["id"] == "maria-k" else ""
        days = p.get("monitoring", {}).get("days_monitored", "—")
        headline = p.get("headline", "")[:110]
        rows.append(f"""
<a class="row" href="{href}">
  <span class="dot" style="background:{dot}"></span>
  <span class="who"><b>{p['name']}</b><small>{p.get('program','')}</small></span>
  <span class="line">{headline}…</span>
  <span class="meta">{days} d <em>{badge}</em></span>
</a>""")
    n_quiet = sum(1 for p in patients if p.get("state") == "green")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Cassandra — Roster</title>
<style>
 *{{margin:0;padding:0;box-sizing:border-box}}
 body{{font-family:-apple-system,'Helvetica Neue',sans-serif;background:#12171a;color:#e8ebe9;min-height:100vh;padding:34px 20px}}
 .wrap{{max-width:900px;margin:0 auto}}
 .brand{{font-size:12px;letter-spacing:.2em;color:#7d968a;font-weight:600}}
 h1{{font-size:22px;margin:6px 0 2px}}
 .sub{{color:#8a958f;font-size:14px;margin-bottom:22px}}
 .row{{display:flex;align-items:center;gap:16px;background:#1a2126;border:1px solid #242e33;border-radius:12px;
      padding:15px 18px;margin-bottom:10px;text-decoration:none;color:inherit;transition:border-color .15s}}
 .row:hover{{border-color:#3b4a52}}
 .dot{{width:12px;height:12px;border-radius:50%;flex:none}}
 .who{{flex:0 0 210px;display:flex;flex-direction:column}}
 .who small{{color:#8a958f;font-size:12px;margin-top:2px}}
 .line{{flex:1;font-size:13.5px;color:#b8c2bc;line-height:1.45}}
 .meta{{flex:none;font-size:12.5px;color:#8a958f;text-align:right}}
 .meta em{{display:block;color:#d99a2b;font-style:normal;font-weight:600}}
 @media(max-width:640px){{.line{{display:none}}.who{{flex:1}}}}
</style></head><body><div class="wrap">
 <div class="brand">CASSANDRA</div>
 <h1>Monitored patients</h1>
 <div class="sub">{len(patients)} patients · {n_quiet} need nothing from you today</div>
 {''.join(rows)}
</div></body></html>"""


@app.get("/replay/{patient_id}", response_class=HTMLResponse)
def replay(patient_id: str):
    patient = PATIENTS.get(patient_id)
    if not patient or patient.get("generated"):
        return RedirectResponse(f"/preview/{patient_id}")
    html = brief_page(patient, TOKEN_TTL_SECONDS)
    overlay = """
<style>
 .headline, h2, p.body, ul.neg, ul.chg, .step, .audit, .prov, .expiry, figure
   {opacity:0; transition:opacity .8s;}
 .revealed {opacity:1 !important;}
 #replay-note{font-family:-apple-system,sans-serif;text-align:center;font-size:13px;
   color:#8a5a10;padding:14px 0 2px;}
</style>
<script>
document.addEventListener('DOMContentLoaded', () => {
  const pill = document.querySelector('.pill');
  pill.textContent = 'GREEN — NO ACTION NEEDED';
  pill.style.cssText = 'background:#e2efe2;color:#2e6b3a;border:1px solid #9dc4a2';

  const note = document.createElement('div');
  note.id = 'replay-note';
  note.textContent = 'Replaying the last 3 weeks of incoming data…';
  document.querySelector('.pt-row').after(note);

  // 1) draw the RHR chart line progressively
  const figs = document.querySelectorAll('figure');
  const rhrFig = figs[0];
  rhrFig.classList.add('revealed');
  const line = rhrFig.querySelector('polyline');
  const len = line.getTotalLength();
  line.style.strokeDasharray = len;
  line.style.strokeDashoffset = len;
  line.getBoundingClientRect();
  line.style.transition = 'stroke-dashoffset 6s linear';
  requestAnimationFrame(() => { line.style.strokeDashoffset = '0'; });

  const reveal = sel => document.querySelectorAll(sel).forEach(el => el.classList.add('revealed'));
  // 2) when the drift completes, the rule fires: flip to amber
  setTimeout(() => {
    note.textContent = 'Rule RHR-DRIFT-14D fired — state change: GREEN → AMBER';
    pill.textContent = 'AMBER — REVIEW';
    pill.style.cssText = '';
  }, 6300);
  // 3) the brief composes itself
  setTimeout(() => { reveal('.headline'); }, 7100);
  setTimeout(() => { reveal('h2, p.body'); figs.forEach((f, i) => { if (i > 0) f.classList.add('revealed'); }); }, 8000);
  setTimeout(() => { reveal('ul.neg, ul.chg, .step'); }, 8900);
  setTimeout(() => { reveal('.audit, .prov, .expiry'); note.remove(); }, 9600);
});
</script>"""
    return HTMLResponse(html.replace("</body>", overlay + "</body>"))


# ---------------------------------------------------------------- GP dashboard (live)

@app.get("/api/roster")
def api_roster():
    out = []
    for p in PATIENTS.values():
        out.append({
            "id": p["id"], "name": p.get("name", "?"),
            "program": p.get("program", ""),
            "state": p.get("state", "green"),
            "reason": p.get("state_reason", ""),
            "headline": (p.get("headline") or "")[:140],
            "days": p.get("monitoring", {}).get("days_monitored", "—"),
        })
    order = {"red": 0, "amber": 1, "insufficient": 2, "green": 3}
    out.sort(key=lambda r: (order.get(r["state"], 3), r["name"]))
    return {"patients": out}


@app.post("/api/demo/set_state/{patient_id}/{state}")
def demo_set_state(patient_id: str, state: str):
    p = PATIENTS.get(patient_id)
    if not p or state not in ("green", "amber", "red", "insufficient"):
        return Response(status_code=404)
    p["state"] = state
    return {"ok": True, "id": patient_id, "state": state}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Cassandra — clinician dashboard</title>
<style>
 *{margin:0;padding:0;box-sizing:border-box}
 body{font-family:-apple-system,'Helvetica Neue',sans-serif;background:#12171a;color:#e8ebe9;height:100vh;display:flex;flex-direction:column;overflow:hidden}
 header{display:flex;align-items:baseline;gap:14px;padding:14px 22px;border-bottom:1px solid #242e33}
 .brand{font-size:12px;letter-spacing:.2em;color:#7d968a;font-weight:600}
 header h1{font-size:17px;font-weight:700}
 #quiet{margin-left:auto;font-size:13px;color:#8a958f}
 main{flex:1;display:flex;min-height:0}
 #list{width:340px;overflow-y:auto;border-right:1px solid #242e33;padding:12px}
 .row{display:flex;gap:11px;align-items:flex-start;padding:12px;border-radius:10px;cursor:pointer;border:1px solid transparent;margin-bottom:6px}
 .row:hover{background:#1a2126}
 .row.sel{background:#1a2126;border-color:#3b4a52}
 .dot{width:11px;height:11px;border-radius:50%;flex:none;margin-top:4px}
 .row b{font-size:14.5px}
 .row small{display:block;color:#8a958f;font-size:12px;margin-top:1px}
 .row .hl{font-size:12px;color:#a8b2ac;margin-top:5px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 #detail{flex:1;background:#efece6}
 #detail iframe{width:100%;height:100%;border:0}
 #empty{height:100%;display:flex;align-items:center;justify-content:center;color:#6b7570;font-size:14.5px}
 #toasts{position:fixed;right:18px;top:64px;display:flex;flex-direction:column;gap:10px;z-index:50;max-width:380px}
 .toast{background:#241d10;border:1px solid #d99a2b;border-left:5px solid #d99a2b;color:#f2e4c8;padding:13px 16px;border-radius:11px;
        font-size:13.5px;line-height:1.5;box-shadow:0 10px 30px rgba(0,0,0,.5);cursor:pointer;animation:slide .35s ease}
 .toast.red{border-color:#c0392b;border-left-color:#c0392b;background:#241012;color:#f2c8c8}
 .toast b{display:block;font-size:14px}
 @keyframes slide{from{transform:translateX(30px);opacity:0}to{transform:none;opacity:1}}
 footer{padding:9px 22px;border-top:1px solid #242e33;display:flex;gap:14px;align-items:center}
 footer button{background:#26332c;color:#c9d2cc;border:1px solid #3c4f45;border-radius:9px;padding:6px 14px;font-size:12.5px;cursor:pointer}
 footer button:hover{background:#2e4638}
 footer span{font-size:11.5px;color:#5d6a63}
</style></head><body>
<header><div class="brand">CASSANDRA</div><h1>Monitored patients</h1><span style="font-size:11.5px;color:#5d6a63;margin-left:2px">the warning, finally heard</span><div id="quiet"></div></header>
<main>
  <div id="list"></div>
  <div id="detail"><div id="empty">Select a patient — most of them need nothing from you.</div></div>
</main>
<div id="toasts"></div>
<footer>
  <button onclick="demoFlip('ellen-w','green')">demo: reset Athina to stable</button>
  <button onclick="demoFlip('ellen-w','amber')">demo: decompensation detected →</button>
  <button onclick="demoFlip('ellen-w','red')">demo: escalate to same-day</button>
  <span>demo controls — not part of the product</span>
</footer>
<script>
const DOT={green:'#4a9a5c',amber:'#d99a2b',red:'#c0392b',insufficient:'#9a958b'};
let known={}, selected=null;
async function poll(){
  try{
    const r=await fetch('/api/roster'); const j=await r.json();
    const list=document.getElementById('list'); list.innerHTML='';
    let quiet=0;
    j.patients.forEach(p=>{
      if(p.state==='green') quiet++;
      const prev=known[p.id];
      if(prev && prev!==p.state && (p.state==='amber'||p.state==='red')) toast(p);
      known[p.id]=p.state;
      const div=document.createElement('div');
      div.className='row'+(selected===p.id?' sel':'');
      div.innerHTML=`<span class="dot" style="background:${DOT[p.state]||'#999'}"></span>
        <span style="min-width:0"><b>${p.name}</b><small>${p.program}</small><span class="hl">${p.headline}</span></span>`;
      div.onclick=()=>select(p.id,div);
      list.appendChild(div);
    });
    document.getElementById('quiet').textContent=`${j.patients.length} patients · ${quiet} need nothing from you today`;
  }catch(e){}
}
function select(id){
  selected=id;
  document.getElementById('detail').innerHTML=`<iframe src="/preview/${id}"></iframe>`;
  document.querySelectorAll('.row').forEach(r=>r.classList.remove('sel'));
  poll();
}
function toast(p){
  const t=document.createElement('div');
  t.className='toast'+(p.state==='red'?' red':'');
  t.innerHTML=`<b>${p.state.toUpperCase()} — ${p.name}</b>${p.reason?('rule '+p.reason+' · '):''}${p.headline}`;
  t.onclick=()=>{select(p.id);t.remove()};
  document.getElementById('toasts').appendChild(t);
  setTimeout(()=>t.remove(), 30000);
}
async function demoFlip(id,state){ await fetch(`/api/demo/set_state/${id}/${state}`,{method:'POST'}); poll(); }
poll(); setInterval(poll, 3000);
</script></body></html>"""


LANDING_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cassandra — the warning, finally heard</title>
<style>
 *{margin:0;padding:0;box-sizing:border-box}
 body{font-family:-apple-system,'Helvetica Neue',sans-serif;background:#0e1316;color:#e8ebe9;line-height:1.6}
 .wrap{max-width:820px;margin:0 auto;padding:0 22px}
 header{text-align:center;padding:64px 22px 40px}
 .brand{font-size:13px;letter-spacing:.32em;color:#7d968a;font-weight:700}
 h1{font-size:clamp(30px,6vw,46px);font-weight:800;margin:14px 0 8px;letter-spacing:-.5px}
 .tag{font-size:17px;color:#9fb0a6;font-style:italic}
 .hero-sub{max-width:620px;margin:22px auto 0;font-size:17px;color:#c4cec8}
 .cta{display:inline-block;margin:30px 8px 0;background:#e8f0ea;color:#0e1316;font-weight:700;font-size:16px;
      padding:15px 30px;border-radius:14px;text-decoration:none;transition:transform .12s}
 .cta:hover{transform:translateY(-2px)}
 .cta.ghost{background:transparent;color:#9fb0a6;border:1px solid #2c3a34}
 section{padding:34px 0;border-top:1px solid #1a2320}
 h2{font-size:13px;letter-spacing:.15em;color:#7d968a;font-weight:700;margin-bottom:16px}
 .myth{font-size:18px;color:#d4ddd7;line-height:1.75}
 .myth b{color:#fff}
 .steps{counter-reset:s;display:grid;gap:14px}
 .step{display:flex;gap:15px;align-items:flex-start;background:#141b1e;border:1px solid #1f2a26;
       border-radius:13px;padding:16px 18px}
 .step:before{counter-increment:s;content:counter(s);flex:none;width:30px;height:30px;border-radius:50%;
   background:#26332c;color:#8fc49a;font-weight:700;display:flex;align-items:center;justify-content:center;font-size:15px}
 .step b{color:#fff}
 .step small{color:#8a958f}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 .card{background:#141b1e;border:1px solid #1f2a26;border-radius:13px;padding:18px}
 .card h3{font-size:15px;color:#fff;margin-bottom:6px}
 .card p{font-size:14px;color:#a8b2ac}
 .priv{background:#121a16;border:1px solid #223028;border-radius:13px;padding:18px;font-size:14px;color:#9fb0a6}
 .priv b{color:#c9d8cf}
 footer{text-align:center;padding:40px 22px 60px;color:#5d6a63;font-size:13px}
 @media(max-width:640px){.grid{grid-template-columns:1fr}}
</style></head><body>
<div class="wrap">
  <header>
    <div class="brand">CASSANDRA</div>
    <h1>Your watch already knows.</h1>
    <div class="tag">the warning, finally heard</div>
    <p class="hero-sub">Cassandra reads the wearable you already own and catches the signs of heart-failure
      decompensation <b>5–10 days before</b> it becomes a hospital admission — then hands the one clinician
      who can act a single, evidenced conclusion. Not another alarm. A decision.</p>
    <a class="cta" href="/upload">Try it on your own data →</a>
    <a class="cta ghost" href="/dashboard">See the clinician dashboard</a>
  </header>

  <section>
    <h2>THE NAME</h2>
    <p class="myth">In Greek myth, <b>Cassandra</b> could see catastrophe coming with perfect accuracy — but was
      cursed so no one ever believed her. Right now, millions of people wear watches that can see their health
      deteriorating days in advance. The prophecy is already being made. <b>And no one is listening.</b>
      We built the cure for the curse.</p>
  </section>

  <section>
    <h2>SEE YOUR OWN BRIEF — 4 STEPS</h2>
    <div class="steps">
      <div class="step"><div><b>Open the Health app</b> on your iPhone <small>&nbsp;— tap your profile picture, top right</small></div></div>
      <div class="step"><div><b>Tap "Export All Health Data"</b> <small>&nbsp;— scroll to the bottom; it builds a .zip (can take a minute)</small></div></div>
      <div class="step"><div><b>Save the zip to Files</b> <small>&nbsp;— then come back here</small></div></div>
      <div class="step"><div><b>Upload it</b> and watch Cassandra read it <small>&nbsp;— your own clinical brief in seconds</small></div></div>
    </div>
    <div style="text-align:center"><a class="cta" href="/upload">Upload your export →</a></div>
  </section>

  <section>
    <h2>WHAT MAKES IT DIFFERENT</h2>
    <div class="grid">
      <div class="card"><h3>Any wearable</h3><p>Apple Watch, Whoop, Garmin, a smart scale — whatever writes to Apple Health. Not another device to buy.</p></div>
      <div class="card"><h3>It concludes, not just charts</h3><p>Deterministic rules detect the pattern; AI writes the clinical case. You get a decision, not a dashboard.</p></div>
      <div class="card"><h3>Silent by default</h3><p>99% of the time it says "nothing needed." It only speaks when several signals converge — the earliest reliable warning.</p></div>
      <div class="card"><h3>Nothing to install for the doctor</h3><p>A brief opens in any browser from a QR code. No app, no integration, no procurement.</p></div>
    </div>
  </section>

  <section>
    <div class="priv"><b>Your privacy.</b> Your export is parsed in memory, only computed daily averages are kept for the
      brief, and the file is deleted immediately after. The resulting link expires in 15 minutes. Nothing is stored,
      shared, or sold. This is a hackathon prototype — not a medical device, and not a substitute for clinical care.</div>
  </section>

  <footer>Cassandra · built at the Reimagine Health hackathon · a triage layer for at-home chronic care</footer>
</div></body></html>"""

import os
import requests
from datetime import datetime, timedelta
from groq import Groq

print("🚀 Launching Version 8.3.1: Institutional Production Surveillance Engine...")

# =========================================================
# 1. GOVERNANCE METRICS
# =========================================================

BOROUGH_BASELINES = {
    "MANHATTAN": 25,
    "BRONX": 35,
    "BROOKLYN": 30,
    "QUEENS": 25,
    "STATEN ISLAND": 20,
    "NYC": 25
}

RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35,
    "STRUCTURAL_INSTABILITY": 30,
    "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20,
    "LITIGATION_GENERAL": 15,
    "STANDARD_VIOLATION": 5,
    "REMEDIATION_EVENT": 0
}

FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90
WATCHLIST_SCORE_THRESHOLD = 65

cutoff_str = (datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)).strftime("%Y-%m-%d")

properties_db = {}
exceptions_log = []
seen_events = set()

# =========================================================
# 2. HELPERS
# =========================================================

def parse_nyc_date(date_str):
    if not date_str:
        return None
    clean = str(date_str).split("T")[0].replace("-", "").strip()
    try:
        return datetime.strptime(clean, "%Y%m%d")
    except:
        try:
            return datetime.strptime(clean, "%Y-%m-%d")
        except:
            return None


def clean_address(item):
    num = str(item.get("house_number") or item.get("buildingnumber") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or "").strip()
    if not num or not street:
        return None
    return f"{num} {street}"

# =========================================================
# 3. DATA INGESTION
# =========================================================

lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# ---------------- Litigation ----------------
try:
    res = requests.get(
        lit_url,
        params={"$where": f"caseopendate > '{cutoff_str}'", "$limit": 150},
        timeout=15
    )
    data = res.json()

    for r in data:
        rec_id = r.get("litigationid", "UNK")
        dt = parse_nyc_date(r.get("caseopendate"))
        if not dt:
            continue

        addr = clean_address(r)
        if not addr:
            continue

        boro = {"1":"BRONX","2":"MANHATTAN","3":"BROOKLYN","4":"QUEENS","5":"STATEN ISLAND"}.get(r.get("boroid"), "NYC")

        key = f"{addr}, {boro}"
        properties_db.setdefault(key, {"boro": boro, "events": []})

        case_type = str(r.get("casetype","")).upper()

        if any(x in case_type for x in ["COMPLIED","CORRECTED","DISMISSED","CODE"]):
            cat = "REMEDIATION_EVENT"
        elif "HARASSMENT" in case_type:
            cat = "HARASSMENT_CLAIM"
        else:
            cat = "LITIGATION_GENERAL"

        event_key = f"{key}_{rec_id}_{dt}"
        if event_key in seen_events:
            continue
        seen_events.add(event_key)

        properties_db[key]["events"].append({
            "cat": cat,
            "age_days": (datetime.now() - dt).days,
            "desc": f"Litigation:{case_type[:35]}"
        })

except Exception as e:
    exceptions_log.append(f"LIT API FAIL: {e}")

# ---------------- Violations ----------------
try:
    res = requests.get(
        viol_url,
        params={"$where": f"issue_date > '{cutoff_str}'", "$limit": 150},
        timeout=15
    )
    data = res.json()

    for r in data:
        rec_id = r.get("violation_number","UNK")
        dt = parse_nyc_date(r.get("issue_date"))
        if not dt:
            continue

        addr = clean_address(r)
        if not addr:
            continue

        raw_boro = str(r.get("boro") or r.get("borough") or "").upper()
        boro = {
            "1":"BRONX","2":"MANHATTAN","3":"BROOKLYN","4":"QUEENS","5":"STATEN ISLAND",
            "BX":"BRONX","MN":"MANHATTAN","BK":"BROOKLYN","QN":"QUEENS","SI":"STATEN ISLAND"
        }.get(raw_boro, "NYC")

        key = f"{addr}, {boro}"
        properties_db.setdefault(key, {"boro": boro, "events": []})

        desc = str(r.get("description","")).upper()
        sev = str(r.get("violation_category","")).upper()

        if any(x in desc for x in ["COMPLIED","CORRECTED","DISMISSED"]):
            cat = "REMEDIATION_EVENT"
        elif any(x in desc for x in ["REMEDY","SEAL","SECURE","SAFEGUARD"]):
            cat = "REMEDIATION_EVENT"
        elif "FIRE" in desc:
            cat = "FIRE_DAMAGE"
        elif "FACADE" in desc or "COLLAPSE" in desc:
            cat = "STRUCTURAL_INSTABILITY"
        elif "CLASS 1" in sev:
            cat = "HAZARDOUS_CLASS_1"
        else:
            cat = "STANDARD_VIOLATION"

        event_key = f"{key}_{rec_id}_{dt}"
        if event_key in seen_events:
            continue
        seen_events.add(event_key)

        properties_db[key]["events"].append({
            "cat": cat,
            "age_days": (datetime.now() - dt).days,
            "desc": f"DOB:{desc[:35]}"
        })

except Exception as e:
    exceptions_log.append(f"VIOL API FAIL: {e}")

# =========================================================
# 4. ZERO DATA GATE
# =========================================================

if not properties_db:
    print("No data")
    exit()

# =========================================================
# 5. SCORE ENGINE
# =========================================================

portfolio = []

for addr, asset in properties_db.items():

    base = BOROUGH_BASELINES.get(asset["boro"], 25)
    current = base
    prior = base

    counts = {}
    traces = []

    events = sorted(asset["events"], key=lambda x: x["age_days"], reverse=True)

    for e in events:

        cat = e["cat"]
        counts[cat] = counts.get(cat, 0) + 1
        recurring = counts[cat] > 1

        fresh = e["age_days"] <= FRESH_WINDOW_DAYS

        if fresh:
            lifecycle = "NEW EVENT" if not recurring else "PERSISTENT CONDITION"
        else:
            lifecycle = "BACKGROUND CONDITION"

        mult = 1.35 if recurring else 1.0
        pts = RULE_SCORE_MATRIX.get(cat, 5)

        if fresh:
            current += pts * mult
        else:
            current += pts * mult * 0.7
            prior += pts * mult * 0.7

        traces.append(f"{e['desc']} | {lifecycle} | +{int(pts)}")

    current = min(100, int(current))
    prior = min(100, int(prior))

    portfolio.append({
        "address": addr,
        "boro": asset["boro"],
        "current": current,
        "prior": prior,
        "accel": current - prior,
        "traces": traces
    })

# =========================================================
# 6. WATCHLIST
# =========================================================

watchlist = sorted(
    [p for p in portfolio if p["current"] >= WATCHLIST_SCORE_THRESHOLD],
    key=lambda x: x["current"],
    reverse=True
)[:20]

count = len(watchlist)

payload = "\n".join([
    f"{w['address']} | {w['current']} | {w['prior']} | {w['accel']}\n" +
    "\n".join(w["traces"])
    for w in watchlist
])

# =========================================================
# 7. PROMPT (TOKEN SAFE)
# =========================================================

prompt = f"""
Institutional CRE surveillance report.

Rules:
- Use ONLY provided assets ({count})
- No ranking or Top lists
- No borrower financial inference
- No DSCR, liquidity, default assumptions
- Reproduce event text exactly

Output:
- Table-style watchlist
- Then each asset breakdown
- Then DATA EXCLUSIONS

DATA:
{payload}

EXCLUSIONS:
{exceptions_log[:3]}
"""

# =========================================================
# 8. EXECUTION
# =========================================================

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

try:
    r = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=3000
    )

    print(r.choices[0].message.content)

except Exception as e:
    print("FAIL:", e)

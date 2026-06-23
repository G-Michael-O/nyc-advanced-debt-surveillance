import os
import requests
from datetime import datetime, timedelta
from groq import Groq

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

DAYS_BACK = 30
cutoff = (datetime.now() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%S")

BOROUGH_MAP = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "STATEN ISLAND"
}

SEVERITY = {
    "FIRE": 35,
    "STRUCTURAL": 30,
    "HAZARDOUS": 20,
    "LITIGATION": 15,
    "VIOLATION": 5
}

print("🚀 V7.1 CLEAN ENGINE STARTING...")

# -----------------------------
# STORAGE
# -----------------------------
assets = {}
exceptions = []

# -----------------------------
# SAFE PARSER (NO FALLBACKS)
# -----------------------------
def parse_addr(item):
    num = (
        item.get("house_number")
        or item.get("housenumber")
        or item.get("address_number")
        or ""
    )

    street = (
        item.get("street_name")
        or item.get("streetname")
        or item.get("street")
        or ""
    )

    num, street = str(num).strip(), str(street).strip()

    if not num or not street:
        exceptions.append(item)
        return None, None

    boro = BOROUGH_MAP.get(str(item.get("boroid") or item.get("boro") or ""), "NYC")
    return f"{num} {street}", boro

# -----------------------------
# LITIGATIONS
# -----------------------------
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"

lit = requests.get(
    lit_url,
    params={"$where": f"caseopendate > '{cutoff}'", "$limit": 300}
).json()

for r in lit:
    addr, boro = parse_addr(r)
    if not addr:
        continue

    assets.setdefault(addr, {"boro": boro, "score": 0, "events": []})

    assets[addr]["score"] += SEVERITY["LITIGATION"]
    assets[addr]["events"].append("LITIGATION")

# -----------------------------
# DOB VIOLATIONS
# -----------------------------
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

viol = requests.get(
    viol_url,
    params={"$where": f"issue_date > '{cutoff}'", "$limit": 300}
).json()

for r in viol:
    addr, boro = parse_addr(r)
    if not addr:
        continue

    desc = (r.get("description") or "").upper()

    assets.setdefault(addr, {"boro": boro, "score": 0, "events": []})

    if "FIRE" in desc:
        assets[addr]["score"] += SEVERITY["FIRE"]
        assets[addr]["events"].append("FIRE")

    elif "STRUCT" in desc or "FACADE" in desc:
        assets[addr]["score"] += SEVERITY["STRUCTURAL"]
        assets[addr]["events"].append("STRUCTURAL")

    elif "HAZ" in desc:
        assets[addr]["score"] += SEVERITY["HAZARDOUS"]
        assets[addr]["events"].append("HAZARDOUS")

    else:
        assets[addr]["score"] += SEVERITY["VIOLATION"]
        assets[addr]["events"].append("VIOLATION")

# -----------------------------
# HARD VALIDATION GATE (CRITICAL FIX)
# -----------------------------
if len(assets) == 0:
    raise Exception("NO VALID PROPERTIES INGESTED — check NYC API fields or parsing logic")

print("TOTAL PROPERTIES INGESTED:", len(assets))
print("DATA QUALITY EXCEPTIONS:", len(exceptions))

# -----------------------------
# SORT TOP ASSETS
# -----------------------------
ranked = sorted(assets.items(), key=lambda x: x[1]["score"], reverse=True)[:10]

payload = ""
for addr, d in ranked:
    payload += f"""
PROPERTY: {addr}
BOROUGH: {d['boro']}
SCORE: {d['score']}
EVENTS: {', '.join(d['events'])}
"""

# -----------------------------
# LLM (FORMAT ONLY — NO LOGIC)
# -----------------------------
prompt = f"""
You are a strict CRE reporting formatter.

RULES:
- DO NOT invent properties
- DO NOT add missing data
- ONLY use provided dataset
- DO NOT hallucinate trends or geography

DATA:
{payload}

Return:
1. Portfolio summary
2. Asset list (same properties only)
3. LinkedIn post (based ONLY on provided data)
"""

response = client.chat.completions.create(
    model="llama-3.1-8b-instant",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.1,
    max_tokens=900
)

print("\n📊 V7.1 OUTPUT")
print(response.choices[0].message.content)

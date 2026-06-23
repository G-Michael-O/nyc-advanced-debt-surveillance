import os
import requests
import pandas as pd
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

print("🚀 V7 CLEAN SURVEILLANCE ENGINE STARTING...")

# -----------------------------
# DATA STORE (TRUTH ONLY)
# -----------------------------
assets = {}
exceptions = []

def clean_addr(item):
    num = item.get("house_number") or item.get("buildingnumber") or ""
    street = item.get("street_name") or item.get("streetname") or ""
    if not num or not street:
        exceptions.append(item)
        return None
    return f"{num} {street}"

# -----------------------------
# LITIGATIONS
# -----------------------------
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"

lit = requests.get(
    lit_url,
    params={"$where": f"caseopendate > '{cutoff}'", "$limit": 500}
).json()

for r in lit:
    addr = clean_addr(r)
    if not addr:
        continue

    boro = BOROUGH_MAP.get(r.get("boroid", ""), "NYC")

    assets.setdefault(addr, {"boro": boro, "score": 0, "events": []})

    assets[addr]["score"] += SEVERITY["LITIGATION"]
    assets[addr]["events"].append("LITIGATION")

# -----------------------------
# DOB VIOLATIONS
# -----------------------------
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

viol = requests.get(
    viol_url,
    params={"$where": f"issue_date > '{cutoff}'", "$limit": 500}
).json()

for r in viol:
    addr = clean_addr(r)
    if not addr:
        continue

    desc = (r.get("description") or "").upper()

    boro = BOROUGH_MAP.get(r.get("boro"), "NYC")

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
# FINAL CLEAN SORT (NO FABRICATION)
# -----------------------------
ranked = sorted(assets.items(), key=lambda x: x[1]["score"], reverse=True)[:10]

summary = ""
for addr, d in ranked:
    summary += f"""
PROPERTY: {addr}
BOROUGH: {d['boro']}
SCORE: {d['score']}
EVENTS: {', '.join(d['events'])}
"""

# -----------------------------
# LLM ONLY FORMATS OUTPUT
# -----------------------------
prompt = f"""
You are a compliance-based CRE reporting formatter.

RULES:
- Do NOT invent properties
- Do NOT add new data
- Only use provided input
- Do NOT extrapolate missing fields

DATA:
{summary}

Write:
1. Portfolio summary (factual only)
2. Asset list (same properties only)
3. LinkedIn post (strictly based on provided data)

No speculation.
"""

response = client.chat.completions.create(
    model="llama-3.1-8b-instant",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.1,
    max_tokens=900
)

print("\n📊 V7 CLEAN OUTPUT")
print(response.choices[0].message.content)

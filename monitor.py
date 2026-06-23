import os
import json
import requests
from datetime import datetime
from groq import Groq

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

DB_FILE = "risk_db.json"

# ---------------- LOAD MEMORY ----------------
if os.path.exists(DB_FILE):
    with open(DB_FILE, "r") as f:
        db = json.load(f)
else:
    db = {}

print("🚀 V7 Institutional Surveillance Engine Starting...")

BOROUGH_BASE = {
    "MANHATTAN": 20,
    "BRONX": 30,
    "BROOKLYN": 25,
    "QUEENS": 20,
    "STATEN ISLAND": 15,
    "NYC": 20
}

WEIGHTS = {
    "FIRE": 35,
    "STRUCTURAL": 30,
    "HARASSMENT": 25,
    "HAZARD": 20,
    "LITIGATION": 15,
    "VIOLATION": 5
}

def classify(desc):
    d = desc.upper()
    if "FIRE" in d:
        return "FIRE"
    if "FACADE" in d or "COLLAPSE" in d:
        return "STRUCTURAL"
    if "HARASSMENT" in d:
        return "HARASSMENT"
    if "HAZARD" in d:
        return "HAZARD"
    return "VIOLATION"


# ---------------- DATA ----------------
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

today = datetime.now().strftime("%Y-%m-%d")

events_today = {}

# LITIGATION
lit = requests.get(lit_url, params={"$limit": 200}).json()

for x in lit:
    addr = x.get("house_number", "") + " " + x.get("street_name", "")
    if addr.strip() == "":
        continue

    events_today.setdefault(addr, {"events": [], "boro": x.get("boroid", "NYC")})

    events_today[addr]["events"].append(
        classify(x.get("casetype", "GENERAL"))
    )

# VIOLATIONS
viol = requests.get(viol_url, params={"$limit": 200}).json()

for x in viol:
    addr = x.get("house_number", "") + " " + x.get("street_name", "")
    if addr.strip() == "":
        continue

    events_today.setdefault(addr, {"events": [], "boro": x.get("boro", "NYC")})

    events_today[addr]["events"].append(
        classify(x.get("description", ""))
    )


# ---------------- SCORING + MEMORY ----------------
report = []

for addr, data in events_today.items():
    boro = data["boro"]
    base = BOROUGH_BASE.get(boro, 20)

    prev = db.get(addr, {
        "history": [],
        "last_score": base
    })

    score = base
    reasons = {}

    for e in data["events"]:
        w = WEIGHTS.get(e, 5)
        reasons[e] = reasons.get(e, 0) + w
        score += w

    score = min(score, 100)

    # ---- TREND ENGINE ----
    last_score = prev["last_score"]
    delta = score - last_score

    if delta > 15:
        trend = "SHOCK DETERIORATION"
    elif delta > 5:
        trend = "WORSENING"
    elif delta >= -5:
        trend = "STABLE"
    else:
        trend = "IMPROVING"

    # ---- SAVE MEMORY ----
    prev["history"].append({
        "date": today,
        "score": score
    })

    prev["last_score"] = score
    db[addr] = prev

    report.append((addr, score, delta, trend, boro))


# ---------------- SAVE DB ----------------
with open(DB_FILE, "w") as f:
    json.dump(db, f, indent=2)


report.sort(key=lambda x: x[1], reverse=True)

top = report[:6]

summary = ""
for r in top:
    summary += f"\n{r[0]} | {r[1]}/100 | Δ{r[2]} | {r[3]} | {r[4]}"


# ---------------- GROQ ANALYSIS ----------------
prompt = f"""
You are a CRE institutional surveillance analyst.

TOP PORTFOLIO MOVERS:
{summary}

Write:
1. Portfolio risk summary
2. Key risk migration insight
3. LinkedIn post (no addresses, <150 words)
"""

res = client.chat.completions.create(
    model="llama-3.1-8b-instant",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.2,
    max_tokens=900
)

print("\n===== V7 OUTPUT =====\n")
print(res.choices[0].message.content)

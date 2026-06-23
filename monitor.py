import os
import requests
from datetime import datetime, timedelta
from groq import Groq

print("🧮 Launching Version 7.4: 100%-Proof Decoupled Surveillance Engine...")

# =====================================================================
# SYSTEM CONFIGURATION & CALCULATED LOOKBACK
# =====================================================================
BOROUGH_BASELINES = {"MANHATTAN": 25, "BRONX": 35, "BROOKLYN": 30, "QUEENS": 25, "STATEN ISLAND": 20, "NYC": 25}
RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35, "STRUCTURAL_INSTABILITY": 30, "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20, "LITIGATION_GENERAL": 15, "STANDARD_VIOLATION": 5
}

FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90

# Format clean server-compliant date string
cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str = cutoff_date.strftime('%Y-%m-%d')

properties_db = {}
exceptions_log = []

def parse_nyc_date(date_str):
    """Safely normalizes incoming municipal date variations."""
    if not date_str: return datetime.now()
    clean_date = str(date_str).split("T")[0].replace("-", "").strip()
    try: return datetime.strptime(clean_date, "%Y%m%d")
    except ValueError:
        try: return datetime.strptime(clean_date, "%Y-%m-%d")
        except Exception: return datetime.now()

def clean_address(item):
    """Validates and formats the physical property key."""
    num = str(item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or item.get("street") or "").strip()
    if not num or not street: return None
    return f"{num} {street}"

# =====================================================================
# LAYER 1: DATA INGESTION ENGINE (WITH LIVE / MOCK AUTO-SWITCH)
# =====================================================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# Ingest Housing Litigations
try:
    res = requests.get(lit_url, params={"$where": f"caseopendate > '{cutoff_str}'", "$order": "caseopendate DESC", "$limit": 150}, timeout=15)
    lit_data = res.json()
    if isinstance(lit_data, list):
        for record in lit_data:
            addr = clean_address(record)
            if not addr: continue
            boro_code = str(record.get("boroid", "NYC"))
            boro_name = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"}.get(boro_code, "NYC")
            full_key = f"{addr}, {boro_name}"
            if full_key not in properties_db: properties_db[full_key] = {"boro": boro_name, "events": []}
            case_type = str(record.get("casetype", "")).upper()
            event_cat = "HARASSMENT_CLAIM" if "HARASSMENT" in case_type else "LITIGATION_GENERAL"
            properties_db[full_key]["events"].append({"cat": event_cat, "age_days": (datetime.now() - parse_nyc_date(record.get("caseopendate"))).days, "desc": f"Litigation: {case_type}"})
except Exception as e: exceptions_log.append(f"Litigation Live Parameter Exception: {e}")

# Ingest DOB Violations
try:
    res = requests.get(viol_url, params={"$where": f"issue_date > '{cutoff_str}'", "$order": "issue_date DESC", "$limit": 150}, timeout=15)
    viol_data = res.json()
    if isinstance(viol_data, list):
        for record in viol_data:
            addr = clean_address(record)
            if not addr: continue
            boro_raw = str(record.get("boro", "NYC")).upper()
            boro_name = "MANHATTAN" if "MANH" in boro_raw else "BRONX" if "BRONX" in boro_raw else "BROOKLYN" if "BROOK" in boro_raw else "QUEENS" if "QUEENS" in boro_raw else "STATEN ISLAND" if "STATEN" in boro_raw else "NYC"
            full_key = f"{addr}, {boro_name}"
            if full_key not in properties_db: properties_db[full_key] = {"boro": boro_name, "events": []}
            desc = str(record.get("description", "")).upper()
            severity = str(record.get("violation_category", "")).upper()
            event_cat = "FIRE_DAMAGE" if "FIRE" in desc else "STRUCTURAL_INSTABILITY" if ("FACADE" in desc or "COLLAPSE" in desc) else "HAZARDOUS_CLASS_1" if ("CLASS 1" in severity or "HAZARDOUS" in severity) else "STANDARD_VIOLATION"
            properties_db[full_key]["events"].append({"cat": event_cat, "age_days": (datetime.now() - parse_nyc_date(record.get("issue_date"))).days, "desc": f"DOB: {desc[:25]}"})
except Exception as e: exceptions_log.append(f"Violation Live Parameter Exception: {e}")

# 🚨 DUAL-LAYER FAIL-SAFE ACTIVATION TRIGGER
if not properties_db or all(len(v["events"]) == 0 for v in properties_db.values()):
    print("⚠️ Live API channels returned an empty tracking set. Engaging local deterministic mock payload...")
    properties_db["170 SOUNDVIEW AVENUE, BRONX"] = {
        "boro": "BRONX",
        "events": [
            {"cat": "FIRE_DAMAGE", "age_days": 8, "desc": "DOB: FIRE DAMAGE STRUCTURAL"},
            {"cat": "STANDARD_VIOLATION", "age_days": 42, "desc": "DOB: ELEVATOR OUT OF SERVICE"}
        ]
    }
    properties_db["505 EAST 184 STREET, BRONX"] = {
        "boro": "BRONX",
        "events": [
            {"cat": "HARASSMENT_CLAIM", "age_days": 14, "desc": "Litigation: HARASSMENT CASE"},
            {"cat": "HAZARDOUS_CLASS_1", "age_days": 18, "desc": "DOB: ILLEGAL CONVERSION EXP"}
        ]
    }
    properties_db["343 EAST 76 STREET, MANHATTAN"] = {
        "boro": "MANHATTAN",
        "events": [{"cat": "LITIGATION_GENERAL", "age_days": 55, "desc": "Litigation: TENANT CASE"}]
    }

# =====================================================================
# LAYER 2: DETERMINISTIC SCORE ENGINE (PURE MATHEMATICS)
# =====================================================================
calculated_portfolio = []

for addr, asset in properties_db.items():
    boro = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)
    current_score = baseline
    historic_score = baseline
    cat_counts = {}
    event_traces = []
    
    sorted_events = sorted(asset["events"], key=lambda x: x["age_days"], reverse=True)
    for ev in sorted_events:
        cat = ev["cat"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        is_recurring = cat_counts[cat] > 1
        amplifier = 1.35 if is_recurring else 1.00
        is_fresh = ev["age_days"] <= FRESH_WINDOW_DAYS
        base_points = RULE_SCORE_MATRIX.get(cat, 5)
        
        if is_fresh:
            points_added = base_points * amplifier
            current_score += points_added
            lifecycle = "NEW_EVENT" if not is_recurring else "RECURRING_ACTIVE"
        else:
            points_added = base_points * amplifier * 0.70
            current_score += points_added
            historic_score += points_added
            lifecycle = "PERSISTENT_BACKGROUND" if not is_recurring else "RECURRING_BACKGROUND"
            
        event_traces.append(f"Type: {ev['desc']} | Code: {lifecycle} | Score Impact: +{int(points_added)}")

    current_score = min(int(current_score), 100)
    historic_score = min(int(historic_score), 100)
    velocity = current_score - historic_score
    
    calculated_portfolio.append({
        "address": addr, "boro": boro, "current": current_score,
        "historic": historic_score, "velocity": velocity, "traces": event_traces
    })

active_watchlist = [a for a in calculated_portfolio if len(a["traces"]) > 0]
active_watchlist = sorted(active_watchlist, key=lambda x: x["current"], reverse=True)[:3]

# =====================================================================
# LAYER 3: LLM SUMMARY & TEXT TRANSLATION GENERATOR
# =====================================================================
data_context_payload = "WATCHLIST ASSETS (DETERMINISTICALLY SCORED BY DETACHED RULE-ENGINE):\n"
for asset in active_watchlist:
    data_context_payload += f"- ADDRESS: {asset['address']}\n"
    data_context_payload += f"  Mathematical Scores: Current={asset['current']}/100, Baseline 30d Ago={asset['historic']}/100, Calculated Velocity={asset['velocity']} points\n"
    data_context_payload += "  Hardcoded Event Logs:\n  " + "\n  ".join(asset['traces']) + "\n\n"

prompt = f"""
You are an executive commercial real estate debt risk reporting compiler. Rephrase this hardcoded mathematical output.
Do NOT perform math calculations. Do NOT change scores. Do NOT add speculative commentary on borrower bankruptcy.

{data_context_payload}

Format exactly into these three plain-text sections:
## 📊 SYSTEM RISK MONITOR MATRIX
[Create a clean Markdown table summarizing the property addresses, current score, historic baseline, and calculated net velocity points]

## 🔍 ASSET RISK COMPLIANCE NOTES
[For each property asset, write a brief bulleted narrative translating the event logs into clear lender terminology]

## 📢 LINKEDIN EXECUTIVE SYNDICATION
Frame the post with this exact opening statement:
"This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow."
Follow with observations regarding risk velocity, shifting trajectories, and the core challenge of isolating material developments between scheduled borrower financial statement reporting cycles. Do NOT mention specific property addresses or private corporate entity titles.
Include these exact hashtags: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
try:
    response = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.0)
    print("\n=====================================================================")
    print(response.choices[0].message.content)
    print("=====================================================================")
except Exception as e: print(f"❌ Layer 3 Translation Engine Failure: {e}")

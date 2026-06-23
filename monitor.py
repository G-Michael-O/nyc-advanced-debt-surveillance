import os
import requests
from datetime import datetime, timedelta
from groq import Groq

# =====================================================================
# SYSTEM INITIALIZATION & HARDCODED CONFIGURATION (LAYER 2: RULES)
# =====================================================================
print("🧮 Launching Version 7.0: Decoupled Rules Engine & Narrative System...")

BOROUGH_BASELINES = {"MANHATTAN": 25, "BRONX": 35, "BROOKLYN": 30, "QUEENS": 25, "STATEN ISLAND": 20, "NYC": 25}

# Deterministic, unchangeable rules engine weight matrix
RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35,
    "STRUCTURAL_INSTABILITY": 30,
    "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20,
    "LITIGATION_GENERAL": 15,
    "STANDARD_VIOLATION": 5
}

# Strict Time Constraints
FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90

cutoff_str = (datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)).strftime('%Y-%m-%dT%H:%M:%S')
properties_db = {}

# =====================================================================
# LAYER 1: DATA INGESTION & DATA QUALITY FILTERS
# =====================================================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
exceptions_log = []

def clean_address(item):
    num = str(item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or item.get("street") or "").strip()
    if not num or not street: return None
    return f"{num} {street}"

# Ingest Litigations
try:
    res = requests.get(lit_url, params={"$where": f"caseopendate > '{cutoff_str}'", "$limit": 300}, timeout=15)
    for record in res.json():
        addr = clean_address(record)
        if not addr:
            exceptions_log.append(f"Litigation ID {record.get('litigationid')}: Missing Address Metadata")
            continue
        
        boro_code = str(record.get("boroid", "NYC"))
        boro_name = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"}.get(boro_code, "NYC")
        full_key = f"{addr}, {boro_name}"
        
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}
            
        case_type = str(record.get("casetype", "")).upper()
        event_cat = "HARASSMENT_CLAIM" if "HARASSMENT" in case_type else "LITIGATION_GENERAL"
        open_date = datetime.strptime(record.get("caseopendate", "").split("T")[0], "%Y-%m-%d")
        
        properties_db[full_key]["events"].append({
            "cat": event_cat, "age_days": (datetime.now() - open_date).days, "desc": f"Litigation: {case_type}"
        })
except Exception as e: print(f"Layer 1 Litigation Bypass: {e}")

# Ingest Violations
try:
    res = requests.get(viol_url, params={"$where": f"issue_date > '{cutoff_str}'", "$limit": 300}, timeout=15)
    for record in res.json():
        addr = clean_address(record)
        if not addr:
            exceptions_log.append(f"Violation ID {record.get('violation_number')}: Missing Address Metadata")
            continue
        
        boro_raw = str(record.get("boro", "NYC")).upper()
        boro_name = "MANHATTAN" if "MANH" in boro_raw else "BRONX" if "BRONX" in boro_raw else "BROOKLYN" if "BROOK" in boro_raw else "QUEENS" if "QUEENS" in boro_raw else "STATEN ISLAND" if "STATEN" in boro_raw else "NYC"
        full_key = f"{addr}, {boro_name}"
        
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}
            
        desc = str(record.get("description", "")).upper()
        severity = str(record.get("violation_category", "")).upper()
        open_date = datetime.strptime(record.get("issue_date", "").split("T")[0], "%Y-%m-%d")
        
        if "FIRE" in desc: event_cat = "FIRE_DAMAGE"
        elif "FACADE" in desc or "COLLAPSE" in desc: event_cat = "STRUCTURAL_INSTABILITY"
        elif "CLASS 1" in severity or "HAZARDOUS" in severity: event_cat = "HAZARDOUS_CLASS_1"
        else: event_cat = "STANDARD_VIOLATION"
        
        properties_db[full_key]["events"].append({
            "cat": event_cat, "age_days": (datetime.now() - open_date).days, "desc": f"DOB: {desc[:25]}"
        })
except Exception as e: print(f"Layer 1 Violation Bypass: {e}")

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
    
    # Sort events chronologically to guarantee deterministic evaluation
    sorted_events = sorted(asset["events"], key=lambda x: x["age_days"], reverse=True)
    
    for ev in sorted_events:
        cat = ev["cat"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        
        # Exact Deterministic Logic Gates
        is_recurring = cat_counts[cat] > 1
        amplifier = 1.35 if is_recurring else 1.00
        is_fresh = ev["age_days"] <= FRESH_WINDOW_DAYS
        
        base_points = RULE_SCORE_MATRIX.get(cat, 5)
        
        if is_fresh:
            # Active in current window
            points_added = base_points * amplifier
            current_score += points_added
            lifecycle = "NEW_EVENT" if not is_recurring else "RECURRING_ACTIVE"
        else:
            # Historical event over 30 days old faces a structural 0.70x decay factor
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

# Filter out assets with no events to isolate active risk rows
active_watchlist = [a for a in calculated_portfolio if len(a["traces"]) > 0]
active_watchlist = sorted(active_watchlist, key=lambda x: x["current"], reverse=True)[:3]

# =====================================================================
# LAYER 3: LLM SUMMARY & TEXT TRANSLATION GENERATOR
# =====================================================================
if not active_watchlist:
    print("\n✅ Deterministic engine verified: No active risk events found in this ingest payload.")
    exit()

# Package data context explicitly to block LLM math hallucination
data_context_payload = "WATCHLIST ASSETS (DETERMINISTICALLY SCORED):\n"
for asset in active_watchlist:
    data_context_payload += f"- ADDRESS: {asset['address']}\n"
    data_context_payload += f"  Mathematical Scores: Current={asset['current']}/100, Baseline 30d Ago={asset['historic']}/100, Calculated Velocity={asset['velocity']} points\n"
    data_context_payload += "  Hardcoded Event Logs:\n  " + "\n  ".join(asset['traces']) + "\n\n"

data_context_payload += f"DATA QUALITY EXCEPTIONS:\n" + "\n".join(exceptions_log[:2]) if exceptions_log else "None\n"

prompt = f"""
You are an executive-level credit data assistant. Summarize this hardcoded mathematical output. 
Do NOT perform math. Do NOT alter scores. Do NOT speculate on borrower insolvency.

{data_context_payload}

Format exactly into these three plain-text sections:
## 📊 SYSTEM RISK MONITOR MATRIX
[Create a simple Markdown table summarizing the addresses, math scores, and calculated velocity metrics]

## 🔍 ASSET RISK COMPLIANCE NOTES
[For each asset, write a brief bulleted narrative translating the hardcoded event codes into clear lender terminology]

## 📢 LINKEDIN EXECUTIVE SYNDICATION
Frame the post with this exact opening line:
"This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow."
Follow with your observations regarding risk velocity, shifting trajectories, and the core challenge of isolating material developments between scheduled borrower financial statement reporting cycles. Do NOT mention specific property addresses or private corporate entity titles.
Include these exact hashtags: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant", messages=[{"role": "user", "content": prompt}], temperature=0.0
    )
    print("\n=====================================================================")
    print(response.choices[0].message.content)
    print("=====================================================================")
except Exception as e: print(f"LLM Generation Block Failed: {e}")

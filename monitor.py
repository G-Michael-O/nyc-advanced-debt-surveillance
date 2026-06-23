import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq

# Initialize Groq client securely using your environment secret
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Configure a wider ingestion window to calculate historical baseline trends (Last 90 Days)
DAYS_BACK = 90
cutoff_date = datetime.now() - timedelta(days=DAYS_BACK)
cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%S')

BOROUGH_MAP = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "STATEN ISLAND"
}

print("🚀 Launching Version 5.0: Temporal Surveillance & Risk Migration Engine...")

# ============================================
# 1. INSTITUTIONAL RISK BASE SEED VALUES
# ============================================
BASE_SEVERITY = {
    "FIRE_DAMAGE": 40,
    "STRUCTURAL_INSTABILITY": 35,
    "HARASSMENT_CLAIM": 25,
    "ACTIVE_LITIGATION_GENERAL": 15,
    "HAZARDOUS_VIOLATION_CLASS_1": 20,
    "STANDARD_VIOLATION": 5
}

properties_tracked = {}
data_quality_exceptions = []

def calculate_temporal_weight(event_date_str):
    """
    Applies an institutional time-decay matrix to the event age.
    0-30 Days: 100% Weight | 31-60 Days: 75% Weight | 61-90 Days: 50% Weight
    """
    try:
        # Standardize NYC OpenData timestamp formats
        clean_date = event_date_str.split("T")[0]
        event_dt = datetime.strptime(clean_date, "%Y-%m-%d")
        age_days = (datetime.now() - event_dt).days
        
        if age_days <= 30:
            return 1.00, age_days, "0-30d [Fresh Velocity]"
        elif age_days <= 60:
            return 0.75, age_days, "31-60d [Stale Residual]"
        elif age_days <= 90:
            return 0.50, age_days, "61-90d [Historical Aging]"
        else:
            return 0.25, age_days, ">90d [Dormant Backlog]"
    except Exception:
        return 1.00, 0, "0-30d [Default Fallback]"

def parse_and_validate_record(item, boro_name, record_id, record_type):
    """Enforces strict asset data quality gates."""
    num = item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("streetname") or item.get("street") or ""
    
    num = str(num).strip()
    street = str(street).strip()
    
    if not num or not street:
        data_quality_exceptions.append({
            "id": record_id,
            "type": record_type,
            "issue": "Incomplete address data. Excluded from risk scoring."
        })
        return None
    return f"{num} {street}, {boro_name}"

# ============================================
# 2. TIME-SERIES DATA INGESTION ENGINE
# ============================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
lit_params = {"$where": f"caseopendate > '{cutoff_str}'", "$limit": 300}

viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
viol_params = {"$where": f"issue_date > '{cutoff_str}'", "$limit": 300}

# Ingest Time-Series Housing Litigations
try:
    lit_res = requests.get(lit_url, params=lit_params, timeout=20)
    lit_data = lit_res.json()
    if isinstance(lit_data, list) and len(lit_data) > 0:
        for item in lit_data:
            rec_id = item.get("litigationid", "UNKNOWN_LIT")
            boro_name = BOROUGH_MAP.get(str(item.get("boroid")), "NYC")
            addr = parse_and_validate_record(item, boro_name, rec_id, "Housing Litigation")
            if not addr: continue  
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"events": [], "historical_baseline_points": 0, "fresh_velocity_points": 0}
            
            case_type = item.get("casetype", "General Litigation")
            event_date_str = item.get("caseopendate", datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
            
            weight, age, window_label = calculate_temporal_weight(event_date_str)
            base_pts = BASE_SEVERITY["HARASSMENT_CLAIM"] if "HARASSMENT" in str(case_type).upper() else BASE_SEVERITY["ACTIVE_LITIGATION_GENERAL"]
            decayed_pts = base_pts * weight
            
            properties_tracked[addr]["events"].append({
                "type": f"Litigation: {case_type}", "age": age, "base": base_pts, "decayed": decayed_pts, "window": window_label
            })
except Exception as e:
    print(f"⚠️ Litigations temporal bypass: {e}")

# Ingest Time-Series DOB Violations
try:
    viol_res = requests.get(viol_url, params=viol_params, timeout=20)
    viol_data = viol_res.json()
    if isinstance(viol_data, list) and len(viol_data) > 0:
        for item in viol_data:
            rec_id = item.get("violation_number", "UNKNOWN_VIOL")
            boro_name = str(item.get("boro", "NYC")).upper()
            addr = parse_and_validate_record(item, boro_name, rec_id, "DOB Violation")
            if not addr: continue  
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"events": [], "historical_baseline_points": 0, "fresh_velocity_points": 0}
            
            description = item.get("description", "Structural Issue").upper()
            severity = str(item.get("violation_category", "")).upper()
            event_date_str = item.get("issue_date", datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
            
            weight, age, window_label = calculate_temporal_weight(event_date_str)
            
            if "FIRE" in description:                  base_pts = BASE_SEVERITY["FIRE_DAMAGE"]
            elif "FACADE" in description or "COLLAPSE" in description: base_pts = BASE_SEVERITY["STRUCTURAL_INSTABILITY"]
            elif "HAZARDOUS" in severity or "CLASS 1" in severity:    base_pts = BASE_SEVERITY["HAZARDOUS_VIOLATION_CLASS_1"]
            else:                                      base_pts = BASE_SEVERITY["STANDARD_VIOLATION"]
            
            decayed_pts = base_pts * weight
            properties_tracked[addr]["events"].append({
                "type": f"DOB: {description[:40]}", "age": age, "base": base_pts, "decayed": decayed_pts, "window": window_label
            })
except Exception as e:
    print(f"⚠️ Violations temporal bypass: {e}")

# ============================================
# 3. RISK MIGRATION ANALYSIS LAYER
# ============================================
portfolio_memos = []

for addr, details in properties_tracked.items():
    fresh_pts = 0       # 0-30 Days rolling window
    historical_pts = 0  # 31-90 Days baseline window
    drivers_log = []
    
    # Process and segment events chronologically to capture risk direction
    for ev in details["events"]:
        if ev["age"] <= 30:
            fresh_pts += ev["decayed"]
            drivers_log.append(f"[FRESH EVOLVING SIGNAL] {ev['type']} (Age: {ev['age']}d, Base: +{ev['base']} pts)")
        else:
            historical_pts += ev["decayed"]
            drivers_log.append(f"[AGING BACKLOG SIGNAL] {ev['type']} (Age: {ev['age']}d, Decayed to: +{ev['decayed']} pts)")

    current_score = min(fresh_pts + historical_pts, 100)
    baseline_score = min(historical_pts, 100)
    risk_migration = current_score - baseline_score
    
    if current_score <= 20:   tier = "Normal Monitoring"
    elif current_score <= 40: tier = "Elevated Monitoring"
    elif current_score <= 69: tier = "Moderate Risk"
    elif current_score <= 84: tier = "High Risk"
    else:                        tier = "Critical Risk"
    
    portfolio_memos.append((addr, {
        "current_score": int(current_score),
        "baseline_score": int(baseline_score),
        "migration": int(risk_migration),
        "tier": tier,
        "drivers": "\n     ".join(drivers_log[:4]) # Cap layout length to avoid token spill
    }))

# Isolate top temporal risk concentrations
portfolio_memos = sorted(portfolio_memos, key=lambda x: x[1]['current_score'], reverse=True)[:5]

payload_summary = ""
for addr, details in portfolio_memos:
    dir_sym = "↑" if details['migration'] > 0 else "→"
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Current Score Tier: {details['tier']} ({details['current_score']}/100)\n"
    payload_summary += f"   - 90-Day Risk Migration: {details['baseline_score']} → {details['current_score']} ({dir_sym} {details['migration']} pts over last 30 days)\n"
    payload_summary += f"   - Verified Time-Series Risk Drivers:\n     {details['drivers']}\n"

exceptions_summary = ""
for exc in data_quality_exceptions[:2]:  
    exceptions_summary += f"⚠️ {exc['type']} ID {exc['id']}: {exc['issue']}\n"

# ============================================
# 4. INSTITUTIONAL ADAPTIVE REPORT COMPILER
# ============================================
analysis_prompt = f"""
You are a senior, compliance-guarded commercial real estate debt surveillance officer writing to an executive credit risk committee. 
Review this time-decayed, risk-migration portfolio payload:

PORTFOLIO TIME-SERIES INTELLIGENCE PAYLOAD:
{payload_summary}

DATA QUALITY GATE RECORD EXCEPTIONS:
{exceptions_summary}

Generate a clear, professional, investment-grade surveillance report adhering exactly to these layout parameters:

1. TEMPORAL SURVEILLANCE MEMORANDUM MATRIX:
   - For every property listed in the payload, build a rigorous credit assessment block matching this taxonomy exactly:
     - PROPERTY: [Full Address]
     - CURRENT RISK TIER: [Tier Name (Current Score/100)]
     - TIME-SERIES RISK MIGRATION: [Baseline Score] to [Current Score] (Indicate point increase/velocity over last 30 days)
     - CRITICAL OPERATIONAL REFLECTION: [Analyze how the fresh events vs the aging backlogs shift asset health. Highlight potential structural capital expenditure shocks for fire/structural issues or regulatory remediation exposure for housing court actions]
     - PROTOCOL ACTION DIRECTIVE: [Draft an asset-specific directive based strictly on the tier: Critical/High requires immediate audit of open filings and verifying remediation. Moderate/Elevated directs baseline monitoring]
   - Guardrail: Maintain measured lender language. Explicitly reinforce that these property-level physical/legal friction events do not establish borrower financial distress or direct debt service defaults without financial statement review.

2. DATA QUALITY GATE RECORD EXCEPTIONS:
   - Output the data exceptions exactly as passed. State that they are excluded from automated scoring pending manual remediation.

3. DISCIPLINED LINKEDIN FEEDS SYNDICATION:
   - Provide an analytical update under 150 words.
   - Guardrail: Do NOT mention specific street addresses, corporate LLC entities, or private borrower names. Do NOT project default assumptions or claim broad borough-wide trends.
   - Guardrail: Match this exact structure and opening statement:
     "Five NYC multifamily assets triggered elevated public-record surveillance signals this week. I built a public-record monitoring workflow that reviews housing litigation and building conditions across NYC multifamily properties. This week's review identified..."
     - Use clean bullet points summarizing the counts of Critical, High, and Moderate assets.
     - Conclude with the formal delineation statement: "Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."
     - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=1500,
        temperature=0.02 # Locked tight to freeze formatting structure
    )
    print("\n========================================================")
    print("📋 CRE SURVEILLANCE PLATFORM: VERSION 5.0 (TEMPORAL RUN)")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e:
    print(f"❌ Time-Series AI Engine failed: {e}")

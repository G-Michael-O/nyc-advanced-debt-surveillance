import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq

# Initialize free Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Configure precise time window (Last 7 days for fresh data ingestion)
DAYS_BACK = 7
cutoff = (datetime.now() - timedelta(days=DAYS_BACK)).strftime('%Y-%m-%dT%H:%M:%S')

BOROUGH_MAP = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "STATEN ISLAND"
}

print("🚀 Launching Version 2.1: Institutional Risk Surveillance Engine...")

# ============================================
# 1. HARDENED INGESTION CONFIGURATIONS
# ============================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
lit_params = {"$where": f"caseopendate > '{cutoff}'", "$limit": 150}

viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
viol_params = {"$where": f"issue_date > '{cutoff}'", "$limit": 150}

properties_tracked = {}

def normalize_address(num, street, boro):
    if not num or not street: return "Unknown Address"
    return f"{str(num).strip()} {str(street).strip()}, {str(boro).strip()}"

# Process Housing Litigations (Weighted Baseline)
try:
    lit_res = requests.get(lit_url, params=lit_params, timeout=20)
    lit_data = lit_res.json()
    if isinstance(lit_data, list) and len(lit_data) > 0:
        for item in lit_data:
            boro_name = BOROUGH_MAP.get(str(item.get("boroid")), "NYC")
            addr = normalize_address(item.get("buildingnumber"), item.get("streetname"), boro_name)
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0}
            
            case_type = item.get("casetype", "General Litigation")
            properties_tracked[addr]["litigations"].append(case_type)
            
            # Calibrated Weighting Matrix
            if "HARASSMENT" in str(case_type).upper():
                properties_tracked[addr]["raw_points"] += 30  # High operational/legal friction
            else:
                properties_tracked[addr]["raw_points"] += 20  # Standard Housing Court Action
except Exception as e:
    print(f"⚠️ Litigations bypass: {e}")

# Process DOB Structural Violations (Weighted Baseline)
try:
    viol_res = requests.get(viol_url, params=viol_params, timeout=20)
    viol_data = viol_res.json()
    if isinstance(viol_data, list) and len(viol_data) > 0:
        for item in viol_data:
            boro_name = str(item.get("boro", "NYC")).upper()
            addr = normalize_address(item.get("house_number"), item.get("street_name"), boro_name)
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0}
            
            description = item.get("description", "Structural Issue")
            severity = str(item.get("violation_category", "")).upper()
            
            properties_tracked[addr]["violations"].append(description)
            
            # Calibrated Severity Weighting Matrix
            if "HAZARDOUS" in severity or "CLASS 1" in severity:
                properties_tracked[addr]["raw_points"] += 35  # Immediately Hazardous (Class 1)
            else:
                properties_tracked[addr]["raw_points"] += 15  # Standard Maintenance/Paperwork Issue
except Exception as e:
    print(f"⚠️ Violations bypass: {e}")

# ============================================
# 2. CALIBRATED SCORING & NORMALIZATION ENGINE
# ============================================
endangered_assets = []
for addr, details in properties_tracked.items():
    # Fix 1: Hard cap total points at 100 to ensure mathematical scale soundness
    calibrated_score = min(details["raw_points"], 100)
    
    # Assign Risk Tiers mathematically
    if calibrated_score <= 20:   tier = "Low Risk"
    elif calibrated_score <= 40: tier = "Elevated Monitoring"
    elif calibrated_score <= 60: tier = "Moderate Risk"
    elif calibrated_score <= 80: tier = "High Risk"
    else:                        tier = "Critical Risk"
    
    endangered_assets.append((addr, {
        "score": calibrated_score,
        "tier": tier,
        "litigations": details["litigations"],
        "violations": details["violations"]
    }))

# Sort assets by score to isolate maximum risk concentrations immediately
endangered_assets = sorted(endangered_assets, key=lambda x: x[1]['score'], reverse=True)[:5]

payload_summary = ""
for addr, details in endangered_assets:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Calibrated Credit Risk Score: {details['score']}/100 [{details['tier']}]\n"
    if details["litigations"]:
        payload_summary += f"   - Active Court Actions: {', '.join(set(details['litigations']))}\n"
    if details["violations"]:
        payload_summary += f"   - Building Maintenance Issues: {'; '.join(set(details['violations'][:2]))}\n"

if not payload_summary:
    payload_summary = "No property-level risk concentrations detected across the five boroughs this week."

# ============================================
# 3. COMPLIANCE-GUARDED AI GENERATION ENGINE
# ============================================
analysis_prompt = f"""
You are a senior, compliance-guarded commercial real estate debt surveillance officer. 
Review this week's property-level public records data for NYC multifamily assets:
{payload_summary}

Generate a strictly professional, investment-grade portfolio report adhering to these boundaries:

1. CREDIT RISK PROFILE ASSESSMENT:
   - Summarize the assets crossing into High-Risk or Critical tiers.
   - Guardrail: Do not make absolute statements regarding borrower liquidity, insolvency, or debt service default. Use measured lender language. Framework: "The accumulation of legal and physical distress indicators may increase the likelihood of operational challenges and elevated capital expenditure requirements. However, borrower liquidity and debt repayment capacity cannot be determined solely from public records and require a review of financial reporting."

2. REVISED ACCURATE ACRIS MONITORING WORKFLOW:
   - Outline a 3-step technical workflow to identify the most recent recorded mortgage holder.
   - Correctly note that an analyst must search Mortgage Class Documents (MTG, Consolidated Mortgages, Assignments - ASST), track the subsequent assignment trail to locate the latest recorded holder, and explicitly state that this represents the recorded lienholder of record, which may differ from the current private economic owner of the debt.

3. INSTITUTIONAL LINKEDIN UPDATE:
   - Draft a professional update under 170 words.
   - Guardrail: Absolutely avoid unverified claims (e.g., claiming public events prove immediate liquidity drops or predict exact debt fund non-performance).
   - Tone: Analytical, institutional, focused on the value of tracking forward-looking operational indicators between formal reporting cycles. Use the exact text style of an institutional asset manager.
   - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=800,
        temperature=0.2  # Reduced temperature to eliminate creative drift and enforce compliance
    )
    print("\n========================================================")
    print("📋 RE SURVEILLANCE REPORT: VERSION 2.1 (CALIBRATED)")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e:
    print(f"❌ AI Generation Engine failed: {e}")

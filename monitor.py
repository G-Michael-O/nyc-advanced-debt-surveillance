import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq

# Initialize Groq client securely using your environment secret
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

print("🚀 Launching Version 3.1: CRE Public Records Intelligence Engine...")

# ============================================
# 1. CALIBRATED SEVERITY SEED VALUES
# ============================================
SEVERITY_MATRIX = {
    "FIRE_DAMAGE": 40,
    "STRUCTURAL_INSTABILITY": 35,
    "HARASSMENT_CLAIM": 25,
    "ACTIVE_LITIGATION_GENERAL": 15,
    "HAZARDOUS_VIOLATION_CLASS_1": 20,
    "STANDARD_VIOLATION": 5
}

properties_tracked = {}
data_quality_exceptions = []

def parse_and_validate_record(item, boro_name, record_id, record_type):
    """Enforces strict asset data quality gates. Incomplete properties are routed to exceptions."""
    num = item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("streetname") or item.get("street") or ""
    
    num = str(num).strip()
    street = str(street).strip()
    
    if not num or not street:
        data_quality_exceptions.append({
            "id": record_id,
            "type": record_type,
            "issue": "Incomplete address data. Excluded from risk scoring pending manual verification."
        })
        return None
    return f"{num} {street}, {boro_name}"

# ============================================
# 2. DATA INGESTION & QUALITY GATES
# ============================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
lit_params = {"$where": f"caseopendate > '{cutoff}'", "$limit": 150}

viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
viol_params = {"$where": f"issue_date > '{cutoff}'", "$limit": 150}

# Process Housing Litigations
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
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0}
            
            case_type = item.get("casetype", "General Litigation")
            properties_tracked[addr]["litigations"].append(case_type)
            
            if "HARASSMENT" in str(case_type).upper():
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["HARASSMENT_CLAIM"]
            else:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["ACTIVE_LITIGATION_GENERAL"]
except Exception as e:
    print(f"⚠️ Litigations bypass: {e}")

# Process DOB Structural Violations
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
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0}
            
            description = item.get("description", "Structural Issue").upper()
            severity = str(item.get("violation_category", "")).upper()
            
            properties_tracked[addr]["violations"].append(description)
            
            if "FIRE" in description:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["FIRE_DAMAGE"]
            elif "FACADE" in description or "COLLAPSE" in description:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["STRUCTURAL_INSTABILITY"]
            elif "HAZARDOUS" in severity or "CLASS 1" in severity:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["HAZARDOUS_VIOLATION_CLASS_1"]
            else:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["STANDARD_VIOLATION"]
except Exception as e:
    print(f"⚠️ Violations bypass: {e}")

# ============================================
# 3. ANALYSIS COMPILER & WORKFLOW ROUTING
# ============================================
endangered_assets = []
for addr, details in properties_tracked.items():
    calibrated_score = min(details["raw_points"], 100) # Capped at 100 max
    
    if calibrated_score <= 20:   tier = "Normal Monitoring"
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

# Isolate top risk concentrations
endangered_assets = sorted(endangered_assets, key=lambda x: x[1]['score'], reverse=True)[:5]

payload_summary = ""
for addr, details in endangered_assets:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Risk Score: {details['score']}/100 [{details['tier']}]\n"
    if details["litigations"]:
        payload_summary += f"   - Court Actions: {', '.join(set(details['litigations']))}\n"
    if details["violations"]:
        payload_summary += f"   - Building Conditions: {'; '.join(set(details['violations'][:2]))}\n"

exceptions_summary = ""
for exc in data_quality_exceptions[:3]:  
    exceptions_summary += f"⚠️ {exc['type']} ID {exc['id']}: {exc['issue']}\n"

if not payload_summary:
    payload_summary = "No property-level risk concentrations detected."

# ============================================
# 4. COMPLIANCE-LOCKED AI TRANSFORMATION
# ============================================
analysis_prompt = f"""
You are a senior, compliance-guarded commercial real estate debt surveillance officer. 
Review this week's property-level public records data for NYC multifamily assets:

PORTFOLIO RISK CONCENTRATIONS:
{payload_summary}

DATA QUALITY GATE RECORD EXCEPTIONS:
{exceptions_summary}

Generate a clean, professional, investment-grade surveillance report adhering strictly to these layout parameters:

1. CREDIT RISK PROFILE ASSESSMENT:
   - Group the assets crossing into Elevated, Moderate, or High-Risk tiers.
   - Guardrail: Maintain measured lender language. Use the framework: "The accumulation of legal and physical distress indicators may increase the likelihood of operational challenges and elevated capital expenditure requirements. However, borrower liquidity and debt repayment capacity cannot be determined solely from public records and require a review of financial reporting."

2. DATA QUALITY GATE RECORD EXCEPTIONS:
   - Output the data exceptions exactly as passed. Note that they are excluded from formal risk scoring pending manual verification.

3. REVISED ACRIS MONITORING WORKFLOW:
   - Output this exact 3-step technical standard to demonstrate credit authority:
     1. Review Mortgage Class Documents (MTG, CEMA, Assignments-ASST): Identify the recorded mortgage holder and review the chain of recorded assignments.
     2. Establish Recorded Lienholder History: Determine the latest recorded holder of record and identify any changes in the public assignment trail.
     3. Escalate Ownership Verification When Necessary: If understanding the current economic owner of the debt is required, supplement public records with servicing reports, lender disclosures, or proprietary data sources.

4. RECOMMENDED MONITORING ACTIONS:
   - For every property listed in the Credit Risk Profile Assessment, output a distinct, operational asset management directive based on its risk tier:
     - For High Risk: "Review recent DOB and legal filings. Determine whether violations remain open or have been remediated. Request updated property condition information if exposure exists. Increase monitoring frequency."
     - For Moderate Risk: "Continue enhanced monitoring. Track whether additional legal or physical issues emerge. Review trends in future reporting periods."
     - For Elevated Monitoring: "Maintain watchlist status. Reassess if additional adverse signals occur."

5. INSTITUTIONAL LINKEDIN UPDATE:
   - Draft an analytical update under 160 words.
   - Guardrail: Do NOT name specific street addresses, specific building identifiers, or individual ownership names. 
   - Guardrail: Frame the update from an independent perspective using this exact opening: "Tracking public records has changed the way I think about CRE risk surveillance. This week, my NYC multifamily public-record surveillance workflow identified..."
   - Guardrail: Do NOT claim borough-wide trends or make default predictions. Frame the signal purely as a trigger for deeper property-level operational review.
   - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=1000,
        temperature=0.10  # Locked down tight to prevent creative interpretation
    )
    print("\n========================================================")
    print("📋 RE SURVEILLANCE REPORT: VERSION 3.1 (PRODUCTION HARDENED)")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e:
    print(f"❌ AI Generation Engine failed: {e}")

import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq

# Initialize free Groq client securely using your GitHub environment secret
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

print("🚀 Launching Version 2.3: Enterprise Risk Surveillance Engine...")

# ============================================
# 1. INSTITUTIONAL SEVERITY MATRIX (0-100 Scale)
# ============================================
SEVERITY_MATRIX = {
    "FIRE DAMAGE": 45,
    "FACADE": 30,
    "RETAINING WALL": 30,
    "STRUCTURAL": 35,
    "HARASSMENT": 40,
    "LITIGATION_GENERAL": 20,
    "VIOLATION_GENERAL": 15
}

properties_tracked = {}
data_quality_exceptions = []

def parse_and_validate_record(item, boro_name, record_id, record_type):
    """Enforces strict data quality controls. Missing addresses are isolated, not scored."""
    num = item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("streetname") or item.get("street") or ""
    
    num = str(num).strip()
    street = str(street).strip()
    
    if not num or not street:
        data_quality_exceptions.append({
            "id": record_id,
            "type": record_type,
            "issue": "Address telemetry incomplete (missing house number or street name)."
        })
        return None
    return f"{num} {street}, {boro_name}"

# ============================================
# 2. DATA INGESTION LAYERS WITH QUALITY GATES
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
            if not addr: continue  # Strict Data Quality Gate
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0}
            
            case_type = item.get("casetype", "General Litigation")
            properties_tracked[addr]["litigations"].append(case_type)
            
            # Map to Severity Matrix
            if "HARASSMENT" in str(case_type).upper():
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["HARASSMENT"]
            else:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["LITIGATION_GENERAL"]
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
            if not addr: continue  # Strict Data Quality Gate
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0}
            
            description = item.get("description", "Structural Issue").upper()
            severity = str(item.get("violation_category", "")).upper()
            
            properties_tracked[addr]["violations"].append(description)
            
            # Contextual Severity Matching
            if "FIRE" in description:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["FIRE DAMAGE"]
            elif "FACADE" in description:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["FACADE"]
            elif "WALL" in description:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["RETAINING WALL"]
            elif "HAZARDOUS" in severity or "CLASS 1" in severity:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["STRUCTURAL"]
            else:
                properties_tracked[addr]["raw_points"] += SEVERITY_MATRIX["VIOLATION_GENERAL"]
except Exception as e:
    print(f"⚠️ Violations bypass: {e}")

# ============================================
# 3. CALIBRATED SCALE RATINGS (0 - 100)
# ============================================
endangered_assets = []
for addr, details in properties_tracked.items():
    calibrated_score = min(details["raw_points"], 100)
    
    # Restructured Strict Scale Framework
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

# Build Structured Summaries
payload_summary = ""
for addr, details in endangered_assets:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Risk Score: {details['score']}/100 [{details['tier']}]\n"
    if details["litigations"]:
        payload_summary += f"   - Court Actions: {', '.join(set(details['litigations']))}\n"
    if details["violations"]:
        payload_summary += f"   - Building Conditions: {'; '.join(set(details['violations'][:2]))}\n"

exceptions_summary = ""
for exc in data_quality_exceptions[:3]:  # Top 3 exceptions for context
    exceptions_summary += f"⚠️ [Data Exception] {exc['type']} ID {exc['id']}: {exc['issue']}\n"

if not payload_summary:
    payload_summary = "No property-level risk concentrations detected."

# ============================================
# 4. COMPLIANCE-GUARDED AI GENERATION ENGINE
# ============================================
analysis_prompt = f"""
You are a senior, compliance-guarded commercial real estate debt surveillance officer. 
Review this week's property-level public records data for NYC multifamily assets:

PORTFOLIO RISK concentrations:
{payload_summary}

DATA QUALITY EXCEPTIONS:
{exceptions_summary}

Generate an institutional portfolio report adhering strictly to these sections and boundaries:

1. CREDIT RISK PROFILE ASSESSMENT:
   - Detail assets crossing into Elevated, Moderate, or High-Risk tiers based on a strict scale (0-20 Normal, 21-40 Elevated, 41-60 Moderate, 61-80 High, 81-100 Critical).
   - Document any Data Quality Exceptions separate from scored assets, noting that they are excluded from formal scoring pending manual data remediation.
   - Guardrail: Maintain measured lender language. Use the framework: "The accumulation of legal and physical distress indicators may increase the likelihood of operational challenges and elevated capital expenditure requirements. However, borrower liquidity and debt repayment capacity cannot be determined solely from public records and require a review of financial reporting."

2. REVISED ACCURATE ACRIS MONITORING WORKFLOW:
   - Reiterate the 3-step technical workflow to identify the most recent recorded mortgage holder using Mortgage Class Documents (MTG, Consolidated Mortgages, Assignments - ASST). Emphasize the distinction between the recorded holder of record and the current private economic owner of the debt.

3. INSTITUTIONAL LINKEDIN UPDATE:
   - Draft a professional update under 170 words.
   - Guardrail: You are NOT employed as a surveillance officer. Frame the update as an independent professional perspective using this exact opening: "Tracking public records has changed the way I think about CRE risk surveillance. This week, my NYC multifamily monitoring workflow identified..."
   - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=900,
        temperature=0.15  # Locked down tight to ensure strict formatting execution
    )
    print("\n========================================================")
    print("📋 RE SURVEILLANCE REPORT: VERSION 2.3 (ENTERPRISE STANDARDS)")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e:
    print(f"❌ AI Generation Engine failed: {e}")

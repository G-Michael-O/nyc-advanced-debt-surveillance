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

print("🚀 Launching Version 4.0: CRE Portfolio Surveillance Memorandum Engine...")

# ============================================
# 1. INSTITUTIONAL SCORING & POLICY MATRIX
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
# 2. DATA INGESTION & RISK COMPILING
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
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0, "drivers": []}
            
            case_type = item.get("casetype", "General Litigation")
            properties_tracked[addr]["litigations"].append(case_type)
            
            if "HARASSMENT" in str(case_type).upper():
                pts = SEVERITY_MATRIX["HARASSMENT_CLAIM"]
                properties_tracked[addr]["raw_points"] += pts
                properties_tracked[addr]["drivers"].append(f"+{pts} Tenant Harassment Claim Filed")
            else:
                pts = SEVERITY_MATRIX["ACTIVE_LITIGATION_GENERAL"]
                properties_tracked[addr]["raw_points"] += pts
                properties_tracked[addr]["drivers"].append(f"+{pts} Unresolved Municipal Litigation")
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
                properties_tracked[addr] = {"litigations": [], "violations": [], "raw_points": 0, "drivers": []}
            
            description = item.get("description", "Structural Issue").upper()
            severity = str(item.get("violation_category", "")).upper()
            
            properties_tracked[addr]["violations"].append(description)
            
            if "FIRE" in description:
                pts = SEVERITY_MATRIX["FIRE_DAMAGE"]
                properties_tracked[addr]["raw_points"] += pts
                properties_tracked[addr]["drivers"].append(f"+{pts} Fire-Related Building Damage")
            elif "FACADE" in description or "COLLAPSE" in description:
                pts = SEVERITY_MATRIX["STRUCTURAL_INSTABILITY"]
                properties_tracked[addr]["raw_points"] += pts
                properties_tracked[addr]["drivers"].append(f"+{pts} Structural/Facade Maintenance Concern")
            elif "HAZARDOUS" in severity or "CLASS 1" in severity:
                pts = SEVERITY_MATRIX["HAZARDOUS_VIOLATION_CLASS_1"]
                properties_tracked[addr]["raw_points"] += pts
                properties_tracked[addr]["drivers"].append(f"+{pts} Hazardous Class-1 Overriding Violation")
            else:
                pts = SEVERITY_MATRIX["STANDARD_VIOLATION"]
                properties_tracked[addr]["raw_points"] += pts
                properties_tracked[addr]["drivers"].append(f"+{pts} Minor Standard Building Violation")
except Exception as e:
    print(f"⚠️ Violations bypass: {e}")

# ============================================
# 3. INTERFACE DATA AGGREGATION
# ============================================
endangered_assets = []
for addr, details in properties_tracked.items():
    calibrated_score = min(details["raw_points"], 100) # Structural mathematical cap
    
    # Standardized Policy Range Protocol Mapping
    if calibrated_score <= 20:
        tier = "Normal Monitoring"
        protocol = "Standard quarterly monitoring portfolio tracking."
    elif calibrated_score <= 40:
        tier = "Elevated Monitoring"
        protocol = "Review new public records during the next monthly surveillance cycle."
    elif calibrated_score <= 69:
        tier = "Moderate Risk"
        protocol = "Enhanced monitoring; review accumulation and aging of adverse signals."
    elif calibrated_score <= 84:
        tier = "High Risk"
        protocol = "Escalate for formal asset-level credit review and physical condition assessment."
    else:
        tier = "Critical Risk"
        protocol = "Immediate executive review of unresolved adverse events and potential collateral impact."
    
    endangered_assets.append((addr, {
        "score": calibrated_score,
        "tier": tier,
        "protocol": protocol,
        "drivers": "\n   ".join(list(set(details["drivers"])))
    }))

# Isolate top risk concentrations
endangered_assets = sorted(endangered_assets, key=lambda x: x[1]['score'], reverse=True)[:5]

payload_summary = ""
for addr, details in endangered_assets:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Current Risk Tier: {details['tier']} ({details['score']}/100)\n"
    payload_summary += f"   - Policy Monitoring Protocol: {details['protocol']}\n"
    payload_summary += f"   - Risk Drivers Breakdown:\n   {details['drivers']}\n"

exceptions_summary = ""
for exc in data_quality_exceptions[:3]:  
    exceptions_summary += f"⚠️ {exc['type']} ID {exc['id']}: {exc['issue']}\n"

if not payload_summary:
    payload_summary = "No property-level risk concentrations detected."

# ============================================
# 4. MEMORANDUM COGNITIVE COMPILER
# ============================================
analysis_prompt = f"""
You are a senior, compliance-guarded commercial real estate debt surveillance officer writing to a bank credit committee. 
Review this structured public records data payload:

PORTFOLIO RAW INTELLIGENCE:
{payload_summary}

DATA QUALITY GATE RECORD EXCEPTIONS:
{exceptions_summary}

Generate an institutional-grade, formal output matching these sections exactly:

1. FORMAL RISK SCORING POLICY REFERENCE:
   - Output a clean reference policy outline for the committee:
     - 0-20: Normal Monitoring (Standard quarterly monitoring)
     - 21-40: Elevated Monitoring (Review new public records during next cycle)
     - 41-69: Moderate Risk (Enhanced monitoring; review accumulation and aging of signals)
     - 70-84: High Risk (Escalate for asset-level review and condition assessment)
     - 85-100: Critical Risk (Immediate review of unresolved adverse events and collateral impact)

2. ASSET-LEVEL SURVEILLANCE MEMORANDUM:
   - For the top flagged properties in the data payload, generate an individual memorandum card block using this precise taxonomy:
     - PROPERTY: [Full Address Block]
     - CURRENT RISK TIER: [Tier Name (Score/100)]
     - RISK DRIVERS EXPLAINABILITY: [Print the explicit additive mathematical point drivers from the data]
     - POTENTIAL CREDIT CONSIDERATIONS: [Detail specific asset implications like potential tenant disruption, regulatory exposure, or future capital expenditure requirements based on whether it is a fire, structural, or legal event]
     - RECOMMENDED ACTION DIRECTIVE: [Draft an asset-specific action directive. High/Critical assets must require auditing open filings and verifying remediation. Moderate/Elevated assets must direct continued baseline trend tracking]
   - Guardrail: Maintain measured lender language. Emphasize that public signals indicate operational/physical friction but do not establish borrower distress, loan impairment, or direct debt-service defaults without financial statement review.

3. DATA QUALITY GATE RECORD EXCEPTIONS:
   - Output the data exceptions exactly as passed. State that they are excluded from formal scoring pending manual data remediation.

4. REVISED ACRIS SURVEILLANCE WORKFLOW:
   - Output these exact 3 structural technical lines:
     - Review Mortgage Class Documents (MTG, CEMA, Assignments-ASST): Identify the recorded mortgage holder and review the chain of recorded assignments.
     - Establish Recorded Lienholder History: Determine the latest recorded holder of record and identify any changes in the public assignment trail.
     - Escalate Ownership Verification When Necessary: If understanding the current economic owner of the debt is required, supplement public records with servicing reports, lender disclosures, or proprietary data sources.

5. INSTITUTIONAL LINKEDIN UPDATE:
   - Draft an analytical update under 150 words.
   - Guardrail: Do NOT name specific street addresses, property LLCs, or individual names. Do NOT claim borough-wide macroeconomic trends or make default predictions based on this small data pool.
   - Guardrail: Frame the update from an independent perspective using this exact opening: "Tracking public records has changed the way I think about CRE risk surveillance. This week, my NYC multifamily public-record surveillance workflow identified..."
   - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=1200,
        temperature=0.05  # Dropped to minimum to freeze absolute structural alignment
    )
    print("\n========================================================")
    print("📋 CRE SURVEILLANCE PLATFORM: VERSION 4.0 OUTPUT")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e:
    print(f"❌ AI Generation Engine failed: {e}")

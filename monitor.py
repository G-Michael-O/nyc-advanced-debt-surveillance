import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq

# Initialize free Groq client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Configure time window (Last 7 days)
DAYS_BACK = 7
cutoff = (datetime.now() - timedelta(days=DAYS_BACK)).strftime('%Y-%m-%dT%H:%M:%S')

BOROUGH_MAP = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "STATEN ISLAND"
}

print("🚀 Launching Advanced Property-Level Risk Intelligence Engine...")

# ============================================
# DATA INGESTION: MICRO PROPERTY DETAIL PULLS
# ============================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
lit_params = {"$where": f"caseopendate > '{cutoff}'", "$limit": 100}

viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
viol_params = {"$where": f"issue_date > '{cutoff}'", "$limit": 100}

properties_tracked = {}

def normalize_address(num, street, boro):
    if not num or not street: return "Unknown Address"
    return f"{str(num).strip()} {str(street).strip()}, {str(boro).strip()}"

# Process Housing Litigations
try:
    lit_res = requests.get(lit_url, params=lit_params, timeout=20)
    lit_data = lit_res.json()
    if isinstance(lit_data, list) and len(lit_data) > 0:
        for item in lit_data:
            boro_name = BOROUGH_MAP.get(str(item.get("boroid")), "NYC")
            addr = normalize_address(item.get("buildingnumber"), item.get("streetname"), boro_name)
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"litigations": [], "violations": [], "risk_score": 0}
            
            case_type = item.get("casetype", "General Litigation")
            properties_tracked[addr]["litigations"].append(case_type)
            properties_tracked[addr]["risk_score"] += 20  # Housing Court Case = +20 pts
except Exception as e:
    print(f"⚠️ Litigations bypass: {e}")

# Process DOB Structural Violations
try:
    viol_res = requests.get(viol_url, params=viol_params, timeout=20)
    viol_data = viol_res.json()
    if isinstance(viol_data, list) and len(viol_data) > 0:
        for item in viol_data:
            boro_name = str(item.get("boro", "NYC")).upper()
            addr = normalize_address(item.get("house_number"), item.get("street_name"), boro_name)
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"litigations": [], "violations": [], "risk_score": 0}
            
            description = item.get("description", "Structural Issue")
            severity = item.get("violation_category", "")
            
            properties_tracked[addr]["violations"].append(description)
            
            if "HAZARDOUS" in severity or "HPD" in severity:
                properties_tracked[addr]["risk_score"] += 30  # Immediately Hazardous = +30 pts
            else:
                properties_tracked[addr]["risk_score"] += 15  # Standard Violation = +15 pts
except Exception as e:
    print(f"⚠️ Violations bypass: {e}")

# ============================================
# RISK ANALYSIS & EXPOSURE ENGINE
# ============================================
endangered_assets = sorted(properties_tracked.items(), key=lambda x: x[1]['risk_score'], reverse=True)[:5]

payload_summary = ""
for addr, details in endangered_assets:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Calculated Distress Score: {details['risk_score']}/100\n"
    if details["litigations"]:
        payload_summary += f"   - Active Court Actions: {', '.join(details['litigations'])}\n"
    if details["violations"]:
        payload_summary += f"   - Building Maintenance Issues: {'; '.join(details['violations'][:2])} (truncated)\n"

if not payload_summary:
    payload_summary = "No property-level risk concentrations detected across the five boroughs this week."

# ============================================
# AI ENRICHMENT LAYER (GROQ ENGINE)
# ============================================
analysis_prompt = f"""
You are a senior commercial real estate debt surveillance officer.
Here is the automated distress profile data extracted from NYC public records for this week:
{payload_summary}

Please generate an institutional-grade portfolio intelligence report containing:
1. RISK PROFILE: Summarize which specific properties are crossing into High-Risk (>40 points) or Critical tiers, what their physical/legal descriptions tell you about the sponsor's liquidity, and how it impacts debt service capabilities.
2. RECORDED LENDER SCREENING WORKFLOW: Outline the exact 3 manual verification steps an analyst should take right now on NYC ACRIS to find the recorded lender of record and underlying mortgage assignments for these specific assets.
3. SCROLL-STOPPING LINKEDIN POST: Write an analytical, data-backed LinkedIn update (under 170 words). 
   - Start with a bold metric hook highlighting that your automated tracker flagged property-level distress points before late payment files.
   - Explain that tracking dynamic operational distress signals (housing litigation + structural violations) reveals sponsor liquidity drops weeks before debt fund non-performance occurs.
   - Do not name real property addresses or private individual names in the post text. Keep it focused on systematic risk modeling.
   - Conclude with an expert question to portfolio managers about forward-looking data feeds.
   - Include these hashtags exactly: #CREFinance #RealEstateLending #RiskManagement #CommercialRealEstate
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=700,
        temperature=0.4
    )
    print("\n========================================================")
    print("📋 ADVANCED RE SURVEILLANCE & CREDIT EXPOSURE REPORT")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e:
    print(f"❌ AI Generation Engine failed: {e}")

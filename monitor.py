import os
import requests
import pandas as pd
from datetime import datetime, timedelta
from groq import Groq

# Initialize Groq client securely using your environment secret
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Ingestion window configured to trace persistent vs fresh activity
DAYS_BACK = 60
cutoff_str = (datetime.now() - timedelta(days=DAYS_BACK)).strftime('%Y-%m-%dT%H:%M:%S')

BOROUGH_MAP = {
    "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"
}

# Baseline submarket operational friction constants (No asset starts at 0 risk)
BOROUGH_BASELINES = {
    "MANHATTAN": 25, "BRONX": 35, "BROOKLYN": 30, "QUEENS": 25, "STATEN ISLAND": 20, "NYC": 25
}

print("🚀 Launching Version 6.0: Portfolio Intelligence & Trajectory Engine...")

# Structural Severity Base Seeds
SEVERITY_WEIGHTS = {
    "FIRE_DAMAGE": 35, "STRUCTURAL_INSTABILITY": 30, "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20, "LITIGATION_GENERAL": 15, "STANDARD_VIOLATION": 5
}

properties_tracked = {}
data_quality_exceptions = []

def parse_and_validate_record(item, boro_id_or_name, record_id, record_type):
    """Enforces rigid data quality controls, filtering incomplete telemetry."""
    num = item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("streetname") or item.get("street") or ""
    num, street = str(num).strip(), str(street).strip()
    
    if not num or not street:
        data_quality_exceptions.append({
            "id": record_id, "type": record_type, "issue": "Incomplete property identifier. Routed to remediation."
        })
        return None, "NYC"
    
    boro_name = BOROUGH_MAP.get(str(boro_id_or_name), str(boro_id_or_name).upper())
    if boro_name not in BOROUGH_BASELINES:
        boro_name = "NYC"
    return f"{num} {street}, {boro_name}", boro_name

# ============================================
# DATA INGESTION LAYERS WITH APPLIED STATE LIFECYCLES
# ============================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# Process Housing Litigations
try:
    lit_res = requests.get(lit_url, params={"$where": f"caseopendate > '{cutoff_str}'", "$limit": 200}, timeout=20)
    for item in lit_res.json():
        rec_id = item.get("litigationid", "UNK")
        addr, boro = parse_and_validate_record(item, item.get("boroid"), rec_id, "Housing Litigation")
        if not addr: continue
        
        if addr not in properties_tracked:
            properties_tracked[addr] = {"events": [], "boro": boro}
            
        case_type = str(item.get("casetype", "General")).upper()
        event_dt = datetime.strptime(item.get("caseopendate", "").split("T")[0], "%Y-%m-%d")
        age = (datetime.now() - event_dt).days
        
        event_cat = "HARASSMENT_CLAIM" if "HARASSMENT" in case_type else "LITIGATION_GENERAL"
        properties_tracked[addr]["events"].append({"cat": event_cat, "age": age, "type": f"Litigation: {case_type}"})
except Exception as e: print(f"⚠️ Litigations structural bypass: {e}")

# Process DOB Violations
try:
    viol_res = requests.get(viol_url, params={"$where": f"issue_date > '{cutoff_str}'", "$limit": 200}, timeout=20)
    for item in viol_res.json():
        rec_id = item.get("violation_number", "UNK")
        addr, boro = parse_and_validate_record(item, item.get("boro"), rec_id, "DOB Violation")
        if not addr: continue
        
        if addr not in properties_tracked:
            properties_tracked[addr] = {"events": [], "boro": boro}
            
        desc = str(item.get("description", "")).upper()
        severity = str(item.get("violation_category", "")).upper()
        event_dt = datetime.strptime(item.get("issue_date", "").split("T")[0], "%Y-%m-%d")
        age = (datetime.now() - event_dt).days
        
        if "FIRE" in desc: event_cat = "FIRE_DAMAGE"
        elif "FACADE" in desc or "COLLAPSE" in desc: event_cat = "STRUCTURAL_INSTABILITY"
        elif "HAZARDOUS" in severity or "CLASS 1" in severity: event_cat = "HAZARDOUS_CLASS_1"
        else: event_cat = "STANDARD_VIOLATION"
        
        properties_tracked[addr]["events"].append({"cat": event_cat, "age": age, "type": f"DOB: {desc[:30]}"})
except Exception as e: print(f"⚠️ Violations structural bypass: {e}")

# ============================================
# ASYMMETRIC TRAJECTORY & LIFECYCLE ENGINE
# ============================================
portfolio_memos = []
total_portfolio_risk_score = 0
borough_acceleration = {}

for addr, details in properties_tracked.items():
    boro = details["boro"]
    baseline_score = BOROUGH_BASELINES[boro] # Initialize via non-zero baseline layer
    
    # Track occurrence frequencies to identify repeat vs isolated events
    cat_counts = {}
    fresh_points = 0
    historical_points = 0
    lifecycle_summary = []
    
    # Process events through chronological and recurrence filters
    for ev in details["events"]:
        cat = ev["cat"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        
        # Determine event lifecycle classification & compound amplification factors
        if cat_counts[cat] > 1:
            lifecycle_status = "Recurring Offense"
            amplifier = 1.35  # Compound risk score by +35% for repeat issues
        elif ev["age"] <= 21:
            lifecycle_status = "New Event"
            amplifier = 1.00
        else:
            lifecycle_status = "Persistent Issue"
            amplifier = 0.70  # Older open issues decay back but remain sticky
            
        base_pts = SEVERITY_WEIGHTS[cat]
        calibrated_pts = base_pts * amplifier
        
        # Apply time decay function based on rolling 21-day velocity gate
        if ev["age"] <= 21:
            fresh_points += calibrated_pts
            lifecycle_summary.append(f"[{lifecycle_status}] {ev['type']} (+{int(calibrated_pts)} pts, Age: {ev['age']}d)")
        else:
            historical_points += calibrated_pts
            lifecycle_summary.append(f"[{lifecycle_status}] {ev['type']} (+{int(calibrated_pts)} pts [Decayed], Age: {ev['age']}d)")

    # Simulate asymmetric non-linear trajectory modeling
    current_score = min(baseline_score + fresh_points + historical_points, 100)
    historic_thirty_day_score = min(baseline_score + historical_points, 100)
    risk_velocity = current_score - historic_thirty_day_score
    
    # Classify directional trajectory based on velocity values
    if risk_velocity > 30:    trajectory = "Sudden Shock Acceleration"
    elif risk_velocity > 0:   trajectory = "Slow Deterioration"
    elif risk_velocity == 0:  trajectory = "Stable Flatline Baseline"
    else:                     trajectory = "Improving Resolution State"
    
    if current_score <= 20:   tier = "Normal Monitoring"
    elif current_score <= 40: tier = "Elevated Monitoring"
    elif current_score <= 69: tier = "Moderate Risk"
    elif current_score <= 84: tier = "High Risk"
    else:                        tier = "Critical Risk"
    
    borough_acceleration[boro] = borough_acceleration.get(boro, 0) + risk_velocity
    total_portfolio_risk_score += current_score
    
    portfolio_memos.append((addr, {
        "current": int(current_score), "historic": int(historic_thirty_day_score),
        "velocity": int(risk_velocity), "tier": tier, "trajectory": trajectory,
        "boro": boro, "logs": "\n     ".join(list(set(lifecycle_summary))[:3])
    }))

# Isolate top active risk drivers
portfolio_memos = sorted(portfolio_memos, key=lambda x: x[1]['current'], reverse=True)[:4]
payload_summary = ""
for addr, details in portfolio_memos:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Current Score Tier: {details['tier']} ({details['current']}/100) | Trajectory: {details['trajectory']}\n"
    payload_summary += f"   - 30-Day Risk Migration: {details['historic']} → {details['current']} (Net Velocity: {details['velocity']} pts)\n"
    payload_summary += f"   - Event Lifecycle Diagnostics:\n     {details['logs']}\n"

# Calculate Systemic Portfolio Insights
high_risk_concentration = sum(1 for x in properties_tracked.values() if len(x['events']) > 2)
fastest_boro = max(borough_acceleration, key=borough_acceleration.get) if borough_acceleration else "None"

portfolio_metrics = f"""
- Total Portfolio Tracked Asset Contenders: {len(properties_tracked)}
- Macro Risk Concentration Gate (Assets with >2 compounding events): {high_risk_concentration} properties
- Highest Velocity Accelerating Submarket Sub-sector: {fastest_boro} Borough Cluster
"""

# ============================================
# COMPLIANCE-INSULATED SYSTEM COMPILER
# ============================================
analysis_prompt = f"""
You are a senior, compliance-guarded commercial real estate debt surveillance officer presenting to an executive risk committee.
Review this time-series risk migration portfolio payload:

SYSTEMIC PORTFOLIO ANALYTICS:
{portfolio_metrics}

ASSET LEVEL TRAJECTORY DIAGNOSTICS:
{payload_summary}

DATA QUALITY GATE RECORD EXCEPTIONS:
{f"{data_quality_exceptions[:2]}" if data_quality_exceptions else "No incomplete property records detected."}

Generate an institutional-grade, professional surveillance dashboard document containing exactly these sections:

1. PORTFOLIO-LEVEL INTELLIGENCE METRICS:
   - Present the macro portfolio statistics. Explain which geographic borough sector is experiencing the fastest risk velocity and what that means for proactive portfolio oversight.

2. ASSET SURVEILLANCE MEMORANDUM MATRIX:
   - For each asset, build a clean structural narrative covering:
     - PROPERTY: [Full Address]
     - RATING STRUCTURE: [Current Risk Tier (Current Score/100)] | TRAJECTORY: [Trajectory Class]
     - RISK MIGRATION PATTERN: [Historic Score] to [Current Score] (Net Velocity: [X] pts)
     - EVENT LIFECYCLE TRACKING DIAGNOSTIC: [Expose the breakdown between New Events, Persistent Issues, or Recurring Offenses as detailed in the data]
     - POTENTIAL CREDIT CONSIDERATIONS: [Detail specific physical structural cap-ex shocks or tenant regulatory exposure based on the specific events logged]
     - MONITORING ACTION PROTOCOL: [High/Critical: Audit open filings, trace remediation history, review structural compliance. Moderate/Elevated: Continue trend-lines monitoring]
   - Guardrail: Maintain measured lender language. Explicitly state that public record operational friction trends do not establish borrower financial distress or loan default probability without concurrent financial statement review.

3. DATA QUALITY GATE RECORD EXCEPTIONS:
   - Log the address exceptions separate from scored assets, noting they are excluded pending data remediation.

4. REVISED ACRIS RECONCILIATION PROTOCOL:
   - Include the exact 3-step assignment tracking workflow standard.

5. PROFESSIONAL FEEDS INDEPENDENT SYNDICATION:
   - Draft an analytical text update under 150 words.
   - Guardrail: Frame the post from an independent professional perspective using this exact opening statement: 
     "This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow."
   - Follow with your unique observations regarding risk velocity, shifting trajectories, and the core challenge of isolating material developments between scheduled borrower financial statement reporting cycles. 
   - Guardrail: Absolutely do NOT name real property street addresses, LLC entities, or private individuals. Do NOT make direct macro default projections.
   - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant", messages=[{"role": "user", "content": analysis_prompt}],
        max_tokens=1500, temperature=0.01
    )
    print("\n========================================================")
    print("📋 CRE SURVEILLANCE PLATFORM: VERSION 6.0 SYSTEM OUTPUT")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e: print(f"❌ Trajectory Engine system failure: {e}")

import os
import requests
from datetime import datetime, timedelta
from groq import Groq

# Initialize Groq client securely using your environment secret
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Configure explicit observation window (90 Days Total)
DAYS_BACK = 90
cutoff_date = datetime.now() - timedelta(days=DAYS_BACK)
cutoff_str = cutoff_date.strftime('%Y-%m-%d') # Clean SoQL string normalization

BOROUGH_MAP = {
    "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"
}

print("🚀 Launching Version 5.0 (Clean): Public Data Signal Engine...")

properties_tracked = {}
data_quality_exceptions = []

def parse_nyc_date(date_str):
    """Normalizes multiple Socrata API timestamp variations into standard datetime."""
    if not date_str:
        return datetime.now()
    clean_date = str(date_str).split("T")[0].replace("-", "").strip()
    try:
        return datetime.strptime(clean_date, "%Y%m%d")
    except ValueError:
        try:
            return datetime.strptime(clean_date, "%Y-%m-%d")
        except Exception:
            return datetime.now()

def parse_and_validate_record(item, boro_name, record_id, record_type):
    """Enforces absolute field quality constraints across municipal records."""
    num = item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or ""
    street = item.get("street_name") or item.get("streetname") or item.get("street") or ""
    
    num, street = str(num).strip(), str(street).strip()
    if not num or not street:
        data_quality_exceptions.append({
            "id": record_id, "type": record_type, "issue": "Incomplete address data metadata."
        })
        return None
    return f"{num} {street}, {boro_name}"

# ============================================
# TIME-SERIES SIGNAL INGESTION LAYER
# ============================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# Ingest Housing Litigations (Ordered by Freshness)
try:
    lit_res = requests.get(lit_url, params={"$where": f"caseopendate > '{cutoff_str}'", "$order": "caseopendate DESC", "$limit": 300}, timeout=20)
    lit_data = lit_res.json()
    if isinstance(lit_data, list):
        for item in lit_data:
            rec_id = item.get("litigationid", "UNK")
            boro_name = BOROUGH_MAP.get(str(item.get("boroid")), "NYC")
            addr = parse_and_validate_record(item, boro_name, rec_id, "Housing Litigation")
            if not addr: continue  
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"fresh_events": [], "historical_events": []}
            
            case_type = item.get("casetype", "General Litigation")
            event_dt = parse_nyc_date(item.get("caseopendate"))
            age_days = (datetime.now() - event_dt).days
            
            event_entry = f"Litigation case filed: {case_type} (Age: {age_days} days)"
            if age_days <= 30:
                properties_tracked[addr]["fresh_events"].append(event_entry)
            else:
                properties_tracked[addr]["historical_events"].append(event_entry)
except Exception as e: print(f"⚠️ Litigations bypass: {e}")

# Ingest DOB Violations (Ordered by Freshness)
try:
    viol_res = requests.get(viol_url, params={"$where": f"issue_date > '{cutoff_str}'", "$order": "issue_date DESC", "$limit": 300}, timeout=20)
    viol_data = viol_res.json()
    if isinstance(viol_data, list):
        for item in viol_data:
            rec_id = item.get("violation_number", "UNK")
            boro_name = str(item.get("boro", "NYC")).upper()
            addr = parse_and_validate_record(item, boro_name, rec_id, "DOB Violation")
            if not addr: continue  
            
            if addr not in properties_tracked:
                properties_tracked[addr] = {"fresh_events": [], "historical_events": []}
            
            description = str(item.get("description", "Standard Violation")).upper()
            event_dt = parse_nyc_date(item.get("issue_date"))
            age_days = (datetime.now() - event_dt).days
            
            event_entry = f"DOB Violation: {description[:35]} (Age: {age_days} days)"
            if age_days <= 30:
                properties_tracked[addr]["fresh_events"].append(event_entry)
            else:
                properties_tracked[addr]["historical_events"].append(event_entry)
except Exception as e: print(f"⚠️ Violations bypass: {e}")

# ============================================
# COMPILATION OF FACTUAL BALANCES
# ============================================
payload_summary = ""
# Isolate assets with high signal density for review
watchlist_assets = sorted(properties_tracked.items(), key=lambda x: len(x[1]["fresh_events"]) + len(x[1]["historical_events"]), reverse=True)[:5]

for addr, details in watchlist_assets:
    payload_summary += f"\n📍 PROPERTY: {addr}\n"
    payload_summary += f"   - Fresh Signal Footprint (0-30 Days): Count = {len(details['fresh_events'])}\n"
    payload_summary += f"   - Historical Signal Footprint (31-90 Days): Count = {len(details['historical_events'])}\n"
    payload_summary += f"   - Activity Log:\n"
    for fe in details["fresh_events"][:2]: payload_summary += f"     * [0-30d Active] {fe}\n"
    for he in details["historical_events"][:2]: payload_summary += f"     * [31-90d Legacy] {he}\n"

exceptions_summary = ""
for exc in data_quality_exceptions[:2]:  
    exceptions_summary += f"⚠️ {exc['type']} ID {exc['id']}: {exc['issue']}\n"

# ============================================
# FACTUAL SUMMARIZATION COMPILER (ZERO FICTION)
# ============================================
analysis_prompt = f"""
You are a credit data processor compiling a clear, factual municipal public records report for a loan surveillance file.
Do NOT calculate risk scores. Do NOT assign arbitrary risk tiers. Do NOT speculate on borrower financial stability or loan default vectors.

FIRM VERIFIED REGULATORY FOOTPRINTS:
{payload_summary}

DATA QUALITY EXCEPTIONS:
{exceptions_summary if exceptions_summary else "None logged."}

Generate an objective public-data tracking brief according to this formatting standard:

1. PUBLIC RECORD REGULATORY ACTIVITY MATRIX:
   For every property listed, compile a factual data summary block using exactly this layout:
   - PROPERTY: [Full Address]
   - CHRONOLOGICAL SIGNAL DISTRIBUTION: [List exactly the count of fresh 0-30 day signals vs historical 31-90 day signals]
   - OBSERVED ADVERSE EVENTS SUMMARY: [Objectively summarize the recorded violations or litigations based on the log entries. Identify specific structural or physical events if explicitly documented in the dataset]
   - REQUIRED VERIFICATION TRACKING: [If an asset exhibits fresh open signals, note that current remediation status requires operational verification against municipal agency filings]

2. DATA QUALITY GATE RECONCILIATION:
   - Output the data exceptions exactly as passed. State that they are excluded from calculations pending missing address updates.

3. DISCIPLINED SYNDICATION REPORTING BLOCK:
   - Provide an analytical text summary under 140 words.
   - Frame the text exactly using this configuration:
     "Five NYC multifamily assets triggered elevated public-record surveillance signals this week. I built a public-record monitoring workflow that reviews housing litigation and building conditions across NYC multifamily properties. This week's review identified..."
   - Objectively state the count of properties with active filings vs those with stable/legacy historic records only.
   - Conclude with this mandatory compliance paragraph: "Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."
   - Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant", messages=[{"role": "user", "content": analysis_prompt}], temperature=0.0
    )
    print("\n========================================================")
    print("📋 CRE MUNICIPAL SIGNAL MONITOR: VERSION 5.0 (CLEAN)")
    print("========================================================")
    print(response.choices[0].message.content)
    print("========================================================")
except Exception as e: print(f"❌ Clean Summary Processing Block Crashed: {e}")

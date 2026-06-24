import os
import requests
from datetime import datetime, timedelta
from groq import Groq

print("🚀 Launching Version 8.3: Institutional Production Surveillance Engine...")

# =====================================================================
# 1. GOVERNANCE-APPROVED CALIBRATION METRICS
# =====================================================================
BOROUGH_BASELINES = {
    "MANHATTAN": 25, "BRONX": 35, "BROOKLYN": 30, "QUEENS": 25, "STATEN ISLAND": 20, "NYC": 25
}

RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35, "STRUCTURAL_INSTABILITY": 30, "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20, "LITIGATION_GENERAL": 15, "STANDARD_VIOLATION": 5,
    "REMEDIATION_EVENT": 0
}

# FIX 3: Surveillance recommendation tier thresholds
SURVEILLANCE_TIERS = {
    (90, 100): ("IMMEDIATE REVIEW",        "Immediate asset management review"),
    (70,  89): ("ENHANCED MONITORING",     "Enhanced monitoring and remediation follow-up"),
    (50,  69): ("STANDARD WATCHLIST",      "Standard watchlist review"),
    ( 0,  49): ("ROUTINE SURVEILLANCE",    "Routine surveillance"),
}

def get_surveillance_tier(score):
    for (low, high), (label, action) in SURVEILLANCE_TIERS.items():
        if low <= score <= high:
            return label, action
    return "ROUTINE SURVEILLANCE", "Routine surveillance"

FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90
WATCHLIST_SCORE_THRESHOLD = 50

cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str = cutoff_date.strftime('%Y-%m-%d')

properties_db = {}
exceptions_log = []

def parse_nyc_date(date_str):
    """Rigorous date validation. Returns None if malformed to prevent false risk flags."""
    if not date_str:
        return None
    clean_date = str(date_str).split("T")[0].replace("-", "").strip()
    try:
        return datetime.strptime(clean_date, "%Y%m%d")
    except ValueError:
        try:
            return datetime.strptime(clean_date, "%Y-%m-%d")
        except Exception:
            return None

def clean_address(item):
    """Validates physical structural identification metadata."""
    num = str(item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or item.get("street") or "").strip()
    if not num or not street:
        return None
    return f"{num} {street}"

# FIX 2: Expand truncated event descriptions into readable credit memo language
EVENT_DESCRIPTION_MAP = {
    "REMEDY: SEAL ALL FIRE DAM": "Remedy – Seal all fire damage and unsafe conditions",
    "STRUCTURE RENDERED NON-CO": "Structure rendered non-compliant",
    "REQUESTING A STRUCTURAL R": "Requesting a structural review",
    "REQUESTING A STRUCTURAL S": "Requesting a structural survey",
    "OBSERVED SWS AT THE FRONT": "Observed sidewalk shed required at front of building",
    "FACADE": "Facade condition requiring inspection or repair",
    "COLLAPSE": "Collapse risk flagged by inspector",
    "HAZARDOUS": "Hazardous condition — Class 1 violation",
    "FIRE": "Fire damage or fire safety condition",
}

def expand_description(raw_desc):
    """Maps truncated API descriptions to full credit memo readable text."""
    upper = raw_desc.upper()
    for key, expanded in EVENT_DESCRIPTION_MAP.items():
        if key in upper:
            return expanded
    # Fallback: title-case the raw description for readability
    return raw_desc.replace("DOB: ", "DOB: ").strip().title()

# =====================================================================
# 2. VALIDATED TIME-SERIES DATA INGESTION ENGINE
# =====================================================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# Ingest Housing Litigations
try:
    res = requests.get(lit_url, params={"$where": f"caseopendate > '{cutoff_str}'", "$order": "caseopendate DESC", "$limit": 150}, timeout=15)
    res.raise_for_status()
    lit_data = res.json()

    if isinstance(lit_data, list):
        for record in lit_data:
            rec_id = record.get("litigationid", "UNK")
            event_date = parse_nyc_date(record.get("caseopendate"))
            if not event_date:
                exceptions_log.append(f"Litigation ID {rec_id}: Excluded due to invalid or null timestamp.")
                continue

            addr = clean_address(record)
            if not addr:
                exceptions_log.append(f"Litigation ID {rec_id}: Excluded due to incomplete address identifiers.")
                continue

            boro_code = str(record.get("boroid", "NYC"))
            boro_name = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"}.get(boro_code, "NYC")
            full_key = f"{addr}, {boro_name}"

            if full_key not in properties_db:
                properties_db[full_key] = {"boro": boro_name, "events": []}

            case_type = str(record.get("casetype", "")).upper()

            if any(k in case_type for k in ["CODE COMPLIANCE", "COMPLIED", "CORRECTED", "DISMISSED"]):
                event_cat = "REMEDIATION_EVENT"
            else:
                event_cat = "HARASSMENT_CLAIM" if "HARASSMENT" in case_type else "LITIGATION_GENERAL"

            properties_db[full_key]["events"].append({
                "cat": event_cat,
                "age_days": (datetime.now() - event_date).days,
                "event_date": event_date,                          # FIX 4: Store raw date for age reporting
                "desc": f"Litigation: {case_type}"
            })
except Exception as e:
    exceptions_log.append(f"Critical Litigation API Failure: {e}")

# Ingest DOB Violations
try:
    res = requests.get(viol_url, params={"$where": f"issue_date > '{cutoff_str}'", "$order": "issue_date DESC", "$limit": 150}, timeout=15)
    res.raise_for_status()
    viol_data = res.json()

    if isinstance(viol_data, list):
        for record in viol_data:
            rec_id = record.get("violation_number", "UNK")
            event_date = parse_nyc_date(record.get("issue_date"))
            if not event_date:
                exceptions_log.append(f"Violation ID {rec_id}: Excluded due to invalid or null timestamp.")
                continue

            addr = clean_address(record)
            if not addr:
                exceptions_log.append(f"Violation ID {rec_id}: Excluded due to incomplete address identifiers.")
                continue

            # FIX 1: Borough classification — handles numeric codes, abbreviations, and full names
            boro_raw = str(record.get("boro", "") or record.get("borough", "") or "").strip().upper()
            BORO_LOOKUP = {
                "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND",
                "MN": "MANHATTAN", "BX": "BRONX", "BK": "BROOKLYN", "QN": "QUEENS", "SI": "STATEN ISLAND",
                "MANHATTAN": "MANHATTAN", "BRONX": "BRONX", "BROOKLYN": "BROOKLYN",
                "QUEENS": "QUEENS", "STATEN ISLAND": "STATEN ISLAND"
            }
            boro_name = next((v for k, v in BORO_LOOKUP.items() if k in boro_raw), "NYC")

            full_key = f"{addr}, {boro_name}"

            if full_key not in properties_db:
                properties_db[full_key] = {"boro": boro_name, "events": []}

            desc = str(record.get("description", "")).upper()
            severity = str(record.get("violation_category", "")).upper()

            if any(k in desc for k in ["CODE COMPLIANCE", "COMPLIED", "CORRECTED", "DISMISSED", "IN CODE-COMPLIAN"]):
                event_cat = "REMEDIATION_EVENT"
            elif "FIRE" in desc:
                event_cat = "FIRE_DAMAGE"
            elif "FACADE" in desc or "COLLAPSE" in desc:
                event_cat = "STRUCTURAL_INSTABILITY"
            elif "CLASS 1" in severity or "HAZARDOUS" in severity:
                event_cat = "HAZARDOUS_CLASS_1"
            else:
                event_cat = "STANDARD_VIOLATION"

            properties_db[full_key]["events"].append({
                "cat": event_cat,
                "age_days": (datetime.now() - event_date).days,
                "event_date": event_date,                          # FIX 4: Store raw date for age reporting
                "desc": f"DOB: {desc[:25]}"
            })
except Exception as e:
    exceptions_log.append(f"Critical Violation API Failure: {e}")

# =====================================================================
# 3. ABSOLUTE ZERO DATA EXCLUSION GATE
# =====================================================================
if not properties_db or all(len(v["events"]) == 0 for v in properties_db.values()):
    print("\n========================================================")
    print("📋 CRE SURVEILLANCE PLATFORM: VERSION 8.3 (PRODUCTION RUN)")
    print("========================================================")
    print("⚠️ No qualifying public-record observations detected during this surveillance period.")
    if exceptions_log:
        print("\nDATA QUALITY EXCEPTIONS:")
        for exc in exceptions_log[:5]:
            print(f"- {exc}")
    print("========================================================")
    exit()

# =====================================================================
# 4. DETERMINISTIC SCORE ENGINE (PURE MATHEMATICS)
# =====================================================================
calculated_portfolio = []

for addr, asset in properties_db.items():
    boro = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)
    current_score = baseline
    historical_component_score = baseline
    cat_counts = {}
    event_traces = []

    sorted_events = sorted(asset["events"], key=lambda x: x["age_days"], reverse=True)
    for ev in sorted_events:
        cat = ev["cat"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        is_recurring = cat_counts[cat] > 1

        lifecycle_fresh = "NEW_EVENT" if not is_recurring else "Recurring Unresolved Condition"
        lifecycle_old = "PERSISTENT_BACKGROUND" if not is_recurring else "Recurring Background Condition"

        amplifier = 1.35 if is_recurring else 1.00
        is_fresh = ev["age_days"] <= FRESH_WINDOW_DAYS
        base_points = RULE_SCORE_MATRIX.get(cat, 5)

        if is_fresh:
            points_added = base_points * amplifier
            current_score += points_added
            lifecycle = lifecycle_fresh
        else:
            points_added = base_points * amplifier * 0.70
            current_score += points_added
            historical_component_score += points_added
            lifecycle = lifecycle_old

        # FIX 2: Expand truncated descriptions
        readable_desc = expand_description(ev["desc"])

        # FIX 4: Add event date and days outstanding to each trace
        event_date_str = ev["event_date"].strftime("%m/%d/%Y") if ev.get("event_date") else "Unknown"
        days_outstanding = ev["age_days"]
        resolution_status = "Unresolved" if cat != "REMEDIATION_EVENT" else "Remediation Recorded"

        event_traces.append(
            f"Type: {readable_desc} | Classification: {lifecycle} | Score Impact: +{int(points_added)} | "
            f"Event Date: {event_date_str} | Days Outstanding: {days_outstanding} | Status: {resolution_status}"
        )

    current_score = min(int(current_score), 100)
    historical_component_score = min(int(historical_component_score), 100)
    velocity = current_score - historical_component_score

    # FIX 3: Assign surveillance tier
    tier_label, tier_action = get_surveillance_tier(current_score)

    # FIX 5: Assign risk direction indicator
    if velocity >= 50:
        trend = "▲▲ Rapid Deterioration"
    elif velocity >= 20:
        trend = "▲ Deteriorating"
    elif velocity == 0:
        trend = "→ Stable"
    elif velocity < 0:
        trend = "▼ Improving"
    else:
        trend = "▲ Mild Deterioration"

    calculated_portfolio.append({
        "address": addr, "boro": boro, "current": current_score,
        "historic_component": historical_component_score, "velocity": velocity,
        "traces": event_traces, "tier_label": tier_label, "tier_action": tier_action,
        "trend": trend
    })

# Threshold-driven watchlist with 20-property safety cap
active_watchlist = [a for a in calculated_portfolio if a["current"] >= WATCHLIST_SCORE_THRESHOLD]
active_watchlist = sorted(active_watchlist, key=lambda x: x["current"], reverse=True)

if len(active_watchlist) > 20:
    active_watchlist = active_watchlist[:20]

# =====================================================================
# 5. NARRATIVE GENERATION COMPILER
# =====================================================================
watchlist_count = len(active_watchlist)

data_context_payload = f"TOTAL_WATCHLIST_COUNT: {watchlist_count}\n"
data_context_payload += "WATCHLIST ASSETS (DETERMINISTICALLY SCORED BY DETACHED RULE-ENGINE):\n"
for asset in active_watchlist:
    data_context_payload += f"- ADDRESS: {asset['address']}\n"
    data_context_payload += (
        f"  Mathematical Scores: Current Collateral Risk Score={asset['current']}/100, "
        f"Pre-Fresh Event Risk State={asset['historic_component']}/100, "
        f"Net Fresh Velocity={asset['velocity']} points, "
        f"Trend={asset['trend']}\n"
        f"  Surveillance Tier: {asset['tier_label']} — {asset['tier_action']}\n"
    )
    data_context_payload += "  Observed Public Record Events:\n  " + "\n  ".join(asset['traces']) + "\n\n"

exceptions_payload = "DATA QUALITY EXCLUSIONS:\n"
if exceptions_log:
    exceptions_payload += "\n".join([f"- {exc}" for exc in exceptions_log[:3]])
else:
    exceptions_payload += "- None"

prompt = f"""
You are an executive commercial real estate debt risk reporting compiler. Rephrase this hardcoded mathematical output into clean institutional lender reporting terminology.

{data_context_payload}

{exceptions_payload}

Format exactly into these three plain-text sections:

## 📊 TEMPORAL SURVEILLANCE MEMORANDUM MATRIX
[Create a simple Markdown table with columns: Property Address | Current Collateral Risk Score | Pre-Fresh Event Risk State | Net Fresh Velocity | Trend | Surveillance Tier]

For each property, follow with these exact narrative headers:
- **PROPERTY**: [Address]
- **CURRENT COLLATERAL RISK SCORE**: [Score]/100
- **SURVEILLANCE TIER**: [Tier Label] — [Recommended Action]
- **OBSERVED PUBLIC RECORD EVENTS**: [List each event with its readable description, event date, days outstanding, and resolution status. Do not truncate.]
- **COLLATERAL MONITORING COMMENTARY**: [Objectively state the tracking of structural filings or litigation. Guardrail: Observed public records indicate unresolved physical or regulatory conditions requiring continued monitoring and review of remediation status. Do not infer severity beyond the event description. Do not assume repairs, tenant displacement, capital expenditure amount, borrower financial stress, or loan default probability. Only restate observed public records.]

## 🔍 DATA QUALITY GATE RECONCILIATION
[Output the data exceptions listed in the payload exactly as passed. State clearly that they are excluded from calculations pending manual file verification.]

WATCHLIST INCLUSION CRITERIA: This report includes all properties scoring at or above {WATCHLIST_SCORE_THRESHOLD}/100 on the deterministic risk scoring engine. Properties scoring below this threshold were processed but excluded from this output. A maximum of 20 properties are displayed per run to maintain prompt integrity. Total properties included in this report: {watchlist_count}.

SURVEILLANCE TIER REFERENCE:
| Risk Score | Tier | Recommended Action |
|---|---|---|
| 90–100 | IMMEDIATE REVIEW | Immediate asset management review |
| 70–89 | ENHANCED MONITORING | Enhanced monitoring and remediation follow-up |
| 50–69 | STANDARD WATCHLIST | Standard watchlist review |
| <50 | ROUTINE SURVEILLANCE | Routine surveillance |

## 📢 DISCIPLINED SYNDICATION SUMMARY
Frame the update under 150 words using this exact opening statement:
"This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow. Based on the exact {watchlist_count} monitored portfolio records scoring at or above the {WATCHLIST_SCORE_THRESHOLD}/100 risk threshold in today's tracking payload, the review identified..."

After the opening statement, do the following:
- Name the single highest scoring asset, its score, and its surveillance tier
- State whether its risk is driven by newly identified signals, recurring unresolved conditions, or both
- Reference the event age and days outstanding for the highest scoring asset to illustrate temporal persistence
- Name the borough with the most flagged assets if more than one borough appears in the watchlist
- Close with one sentence on what this signals for collateral monitoring cadence

Guardrails:
- Do not calculate or infer portfolio counts. Use only the exact asset counts provided in the system payload ({watchlist_count}).
- Do not include excluded data-quality records as monitored assets.
- Do not use non-standard banking phrases like 'fresh background metrics' or 'legacy background metrics'. Rephrase to 'newly identified adverse public-record signals' or 'older persistent conditions'.
- Do not repeat the compliance paragraph verbatim in the body. It appears only at the end.

Conclude with this mandatory compliance paragraph: "Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."
Include these hashtags exactly: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    print("\n=====================================================================")
    print(response.choices[0].message.content)
    print("=====================================================================")
except Exception as e:
    print(f"❌ Layer 5 Summary Compiling Block Failure: {e}")

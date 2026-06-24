import os
import re
import math
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from groq import Groq

print("🚀 Launching Version 8.4: Institutional Production Surveillance Engine...")

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

# FIX 3: Institutional risk buckets replacing hard threshold
RISK_BUCKETS = {
    (80, 100): ("CRITICAL",  "Immediate asset management review and escalation"),
    (65,  79): ("ELEVATED",  "Enhanced monitoring and remediation follow-up"),
    (50,  64): ("WATCH",     "Standard watchlist review"),
    ( 0,  49): ("MONITOR",   "Routine surveillance"),
}

def get_risk_bucket(score):
    for (low, high), (label, action) in RISK_BUCKETS.items():
        if low <= score <= high:
            return label, action
    return "MONITOR", "Routine surveillance"

# FIX 7: Escalation rules engine
ESCALATION_RULES = [
    {
        "id": "RULE_FIRE_STRUCTURAL_COMBO",
        "description": "Fire + Structural combo detected",
        "condition": lambda fp: fp["fire_events"] > 0 and fp["structural_events"] > 0,
        "action": "AUTO ESCALATE → IMMEDIATE REVIEW"
    },
    {
        "id": "RULE_RAPID_FRESH_CLUSTER",
        "description": "2+ unresolved events within 30 days",
        "condition": lambda fp: fp["fresh_event_count"] >= 2,
        "action": "ESCALATE TIER → ENHANCED MONITORING"
    },
    {
        "id": "RULE_OPERATIONAL_DECAY",
        "description": "Recurring condition flagged 3+ times",
        "condition": lambda fp: fp["recurrence_index"] >= 3,
        "action": "FLAG → OPERATIONAL DECAY PATTERN"
    },
]

def evaluate_escalation_rules(fingerprint):
    triggered = []
    for rule in ESCALATION_RULES:
        if rule["condition"](fingerprint):
            triggered.append(f"{rule['id']}: {rule['description']} → {rule['action']}")
    return triggered

FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90
WATCHLIST_SCORE_THRESHOLD = 50
MAX_PROPERTIES_PER_PROMPT = 10   # FIX 8: Token overflow control
MAX_EVENTS_PER_PROPERTY = 5      # FIX 8: Token overflow control
MAX_DESC_WORDS = 12              # FIX 5: Evidence compression

cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str = cutoff_date.strftime('%Y-%m-%d')

properties_db = {}
exceptions_log = []

# =====================================================================
# 2. UTILITY FUNCTIONS
# =====================================================================
def parse_nyc_date(date_str):
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
    num = str(item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or item.get("street") or "").strip()
    if not num or not street:
        return None
    return f"{num} {street}"

# FIX 1: Standardized event schema classifier
def classify_event(raw_desc, severity="", case_type=""):
    desc = raw_desc.upper()
    sev  = severity.upper()
    ct   = case_type.upper()

    if any(k in desc or k in ct for k in ["CODE COMPLIANCE", "COMPLIED", "CORRECTED", "DISMISSED", "IN CODE-COMPLIAN"]):
        return "REMEDIATION_EVENT", "Remediation Recorded", "REMEDIATED"
    elif "FIRE" in desc:
        return "FIRE_DAMAGE", "Fire Damage or Safety Condition", "OPEN"
    elif "FACADE" in desc or "COLLAPSE" in desc:
        return "STRUCTURAL_INSTABILITY", "Facade or Structural Instability", "OPEN"
    elif "CLASS 1" in sev or "HAZARDOUS" in sev:
        return "HAZARDOUS_CLASS_1", "Hazardous Condition Class 1", "OPEN"
    elif "HARASSMENT" in ct:
        return "HARASSMENT_CLAIM", "Tenant Harassment Claim", "OPEN"
    elif ct and ct not in ["", "UNK"]:
        return "LITIGATION_GENERAL", "General Housing Litigation", "OPEN"
    else:
        return "STANDARD_VIOLATION", "Standard DOB Violation", "OPEN"

# FIX 5: Evidence compression — truncate to max words
def compress_description(text, max_words=MAX_DESC_WORDS):
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]) + "..."

# FIX 5: Deduplicate recurring event patterns before prompt assembly
def deduplicate_events(events, max_events=MAX_EVENTS_PER_PROPERTY):
    seen = {}
    deduped = []
    for ev in events:
        key = ev["event_type"]
        if key not in seen:
            seen[key] = 0
        seen[key] += 1
        if seen[key] <= 2:  # allow max 2 of same type, flag recurrence
            deduped.append(ev)
    return deduped[:max_events]

# =====================================================================
# 3. VALIDATED TIME-SERIES DATA INGESTION ENGINE
# =====================================================================
lit_url  = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

BORO_LOOKUP = {
    "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND",
    "MN": "MANHATTAN", "BX": "BRONX", "BK": "BROOKLYN", "QN": "QUEENS", "SI": "STATEN ISLAND",
    "MANHATTAN": "MANHATTAN", "BRONX": "BRONX", "BROOKLYN": "BROOKLYN",
    "QUEENS": "QUEENS", "STATEN ISLAND": "STATEN ISLAND"
}

# Ingest Housing Litigations
try:
    res = requests.get(lit_url, params={
        "$where": f"caseopendate > '{cutoff_str}'",
        "$order": "caseopendate DESC", "$limit": 150
    }, timeout=15)
    res.raise_for_status()
    for record in res.json():
        rec_id     = record.get("litigationid", "UNK")
        event_date = parse_nyc_date(record.get("caseopendate"))
        if not event_date:
            exceptions_log.append(f"Litigation ID {rec_id}: Invalid timestamp.")
            continue
        addr = clean_address(record)
        if not addr:
            exceptions_log.append(f"Litigation ID {rec_id}: Incomplete address.")
            continue
        boro_code = str(record.get("boroid", "NYC"))
        boro_name = {"1": "MANHATTAN","2": "BRONX","3": "BROOKLYN","4": "QUEENS","5": "STATEN ISLAND"}.get(boro_code, "NYC")
        full_key  = f"{addr}, {boro_name}"
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}

        case_type               = str(record.get("casetype", ""))
        event_type, event_class, event_status = classify_event("", "", case_type)

        properties_db[full_key]["events"].append({
            "event_type":   event_type,
            "event_class":  event_class,
            "event_status": event_status,
            "impact_score": RULE_SCORE_MATRIX.get(event_type, 5),
            "age_days":     (datetime.now() - event_date).days,
            "event_date":   event_date,
            "raw_desc":     compress_description(f"Litigation: {case_type}")
        })
except Exception as e:
    exceptions_log.append(f"Critical Litigation API Failure: {e}")

# Ingest DOB Violations
try:
    res = requests.get(viol_url, params={
        "$where": f"issue_date > '{cutoff_str}'",
        "$order": "issue_date DESC", "$limit": 150
    }, timeout=15)
    res.raise_for_status()
    for record in res.json():
        rec_id     = record.get("violation_number", "UNK")
        event_date = parse_nyc_date(record.get("issue_date"))
        if not event_date:
            exceptions_log.append(f"Violation ID {rec_id}: Invalid timestamp.")
            continue
        addr = clean_address(record)
        if not addr:
            exceptions_log.append(f"Violation ID {rec_id}: Incomplete address.")
            continue

        boro_raw  = str(record.get("boro", "") or record.get("borough", "") or "").strip().upper()
        boro_name = next((v for k, v in BORO_LOOKUP.items() if k in boro_raw), "NYC")
        full_key  = f"{addr}, {boro_name}"
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}

        desc     = str(record.get("description", ""))
        severity = str(record.get("violation_category", ""))
        event_type, event_class, event_status = classify_event(desc, severity)

        properties_db[full_key]["events"].append({
            "event_type":   event_type,
            "event_class":  event_class,
            "event_status": event_status,
            "impact_score": RULE_SCORE_MATRIX.get(event_type, 5),
            "age_days":     (datetime.now() - event_date).days,
            "event_date":   event_date,
            "raw_desc":     compress_description(f"DOB: {desc}")
        })
except Exception as e:
    exceptions_log.append(f"Critical Violation API Failure: {e}")

# =====================================================================
# 4. ZERO DATA EXCLUSION GATE
# =====================================================================
if not properties_db or all(len(v["events"]) == 0 for v in properties_db.values()):
    print("\n========================================================")
    print("📋 CRE SURVEILLANCE PLATFORM: VERSION 8.4 (PRODUCTION RUN)")
    print("========================================================")
    print("⚠️ No qualifying public-record observations detected.")
    for exc in exceptions_log[:5]:
        print(f"- {exc}")
    print("========================================================")
    exit()

# =====================================================================
# 5. DETERMINISTIC SCORE ENGINE + PROPERTY FINGERPRINT (FIX 4 + 6)
# =====================================================================
calculated_portfolio = []

# FIX 9 OPTIONAL: Borough population tracking for z-score anomaly detection
borough_scores = defaultdict(list)

for addr, asset in properties_db.items():
    boro     = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)

    # FIX 4: Separate lifetime stress from 30-day momentum
    lifetime_score   = baseline
    momentum_score   = 0   # fresh 30-day delta only
    cat_counts       = {}
    structured_events = []

    sorted_events = sorted(asset["events"], key=lambda x: x["age_days"], reverse=True)

    for ev in sorted_events:
        cat      = ev["event_type"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        is_recurring    = cat_counts[cat] > 1
        amplifier       = 1.35 if is_recurring else 1.00
        is_fresh        = ev["age_days"] <= FRESH_WINDOW_DAYS
        base_points     = ev["impact_score"]

        if is_recurring:
            event_class_tag = "Recurring Unresolved Condition" if is_fresh else "Recurring Background Condition"
        else:
            event_class_tag = "NEW_EVENT" if is_fresh else "PERSISTENT_BACKGROUND"

        points = base_points * amplifier
        if is_fresh:
            momentum_score += points
        lifetime_score += points * (1.0 if is_fresh else 0.70)

        structured_events.append({
            "event_type":    cat,
            "event_class":   event_class_tag,
            "event_status":  ev["event_status"],
            "impact_score":  int(points),
            "event_date":    ev["event_date"].strftime("%m/%d/%Y") if ev.get("event_date") else "Unknown",
            "days_outstanding": ev["age_days"],
            "description":   ev["raw_desc"],
            "is_fresh":      is_fresh
        })

    current_score  = min(int(lifetime_score), 100)
    momentum_score = min(int(momentum_score), 100)
    velocity       = momentum_score  # FIX 4: velocity = 30-day momentum only

    # FIX 6: Property fingerprint
    fingerprint = {
        "fire_events":       sum(1 for e in structured_events if e["event_type"] == "FIRE_DAMAGE"),
        "structural_events": sum(1 for e in structured_events if e["event_type"] == "STRUCTURAL_INSTABILITY"),
        "fresh_event_count": sum(1 for e in structured_events if e["is_fresh"]),
        "recurrence_index":  sum(1 for e in structured_events if "Recurring" in e["event_class"]),
        "recency_score":     momentum_score,
        "lifetime_stress":   current_score,
        "dominant_type":     max(cat_counts, key=cat_counts.get) if cat_counts else "NONE"
    }

    # FIX 7: Evaluate escalation rules
    escalation_flags = evaluate_escalation_rules(fingerprint)

    # Risk bucket
    bucket_label, bucket_action = get_risk_bucket(current_score)

    # Trend
    if velocity >= 50:
        trend = "▲▲ Rapid Deterioration"
    elif velocity >= 20:
        trend = "▲ Deteriorating"
    elif velocity > 0:
        trend = "▲ Mild Deterioration"
    elif velocity == 0:
        trend = "→ Stable"
    else:
        trend = "▼ Improving"

    # FIX 5: Deduplicate events before storing
    compressed_events = deduplicate_events(structured_events)

    result = {
        "address":           addr,
        "boro":              boro,
        "current":           current_score,
        "momentum":          momentum_score,
        "velocity":          velocity,
        "trend":             trend,
        "bucket_label":      bucket_label,
        "bucket_action":     bucket_action,
        "fingerprint":       fingerprint,
        "escalation_flags":  escalation_flags,
        "events":            compressed_events
    }
    calculated_portfolio.append(result)
    borough_scores[boro].append(current_score)

# =====================================================================
# 6. OPTIONAL: ANOMALY DETECTION — BOROUGH Z-SCORE
# =====================================================================
def compute_z_score(score, scores_list):
    if len(scores_list) < 2:
        return None
    mean = sum(scores_list) / len(scores_list)
    std  = math.sqrt(sum((s - mean) ** 2 for s in scores_list) / len(scores_list))
    if std == 0:
        return 0.0
    return round((score - mean) / std, 2)

for asset in calculated_portfolio:
    scores_in_boro   = borough_scores[asset["boro"]]
    asset["z_score"] = compute_z_score(asset["current"], scores_in_boro)
    asset["anomaly"] = "⚠️ ANOMALY" if asset["z_score"] and asset["z_score"] >= 1.5 else ""

# =====================================================================
# 7. RISK CLUSTERING BY DOMINANT EVENT TYPE
# =====================================================================
cluster_map = defaultdict(list)
for asset in calculated_portfolio:
    cluster_map[asset["fingerprint"]["dominant_type"]].append(asset["address"])

# =====================================================================
# 8. WATCHLIST FILTER + TOKEN-SAFE PROMPT ASSEMBLY (FIX 8)
# =====================================================================
active_watchlist = [a for a in calculated_portfolio if a["current"] >= WATCHLIST_SCORE_THRESHOLD]
active_watchlist = sorted(active_watchlist, key=lambda x: x["current"], reverse=True)
active_watchlist = active_watchlist[:MAX_PROPERTIES_PER_PROMPT]
watchlist_count  = len(active_watchlist)

# Portfolio systemic risk index
if active_watchlist:
    systemic_index = round(sum(a["current"] for a in active_watchlist) / watchlist_count, 1)
else:
    systemic_index = 0.0

# FIX 8: Compressed payload — no raw dumps, max events enforced
data_context_payload = f"WATCHLIST_COUNT: {watchlist_count} | SYSTEMIC_RISK_INDEX: {systemic_index}/100\n\n"
for asset in active_watchlist:
    fp = asset["fingerprint"]
    data_context_payload += (
        f"PROPERTY: {asset['address']} ({asset['boro']})\n"
        f"  SCORES: Current={asset['current']}/100 | 30-Day Momentum={asset['momentum']}/100 | "
        f"Velocity={asset['velocity']} pts | Trend={asset['trend']}\n"
        f"  RISK BUCKET: {asset['bucket_label']} — {asset['bucket_action']}\n"
        f"  Z-SCORE vs BOROUGH: {asset['z_score']} {asset['anomaly']}\n"
        f"  FINGERPRINT: Fire={fp['fire_events']} | Structural={fp['structural_events']} | "
        f"Fresh Events={fp['fresh_event_count']} | Recurrence Index={fp['recurrence_index']} | "
        f"Dominant Type={fp['dominant_type']}\n"
    )
    if asset["escalation_flags"]:
        data_context_payload += f"  ESCALATION FLAGS:\n"
        for flag in asset["escalation_flags"]:
            data_context_payload += f"    ⚠️ {flag}\n"
    data_context_payload += "  EVENTS (max 5, deduplicated):\n"
    for ev in asset["events"]:
        data_context_payload += (
            f"    - TYPE: {ev['event_type']} | CLASS: {ev['event_class']} | "
            f"STATUS: {ev['event_status']} | IMPACT: +{ev['impact_score']} | "
            f"DATE: {ev['event_date']} | DAYS OUT: {ev['days_outstanding']} | "
            f"DESC: {ev['description']}\n"
        )
    data_context_payload += "\n"

# Cluster summary
cluster_summary = "RISK CLUSTERS BY EVENT TYPE:\n"
for cluster_type, addresses in cluster_map.items():
    if cluster_type != "NONE":
        cluster_summary += f"  {cluster_type}: {len(addresses)} properties\n"

exceptions_payload = "DATA QUALITY EXCLUSIONS (sample):\n"
exceptions_payload += "\n".join([f"  - {e}" for e in exceptions_log[:3]]) if exceptions_log else "  - None"

# =====================================================================
# 9. LLM NARRATIVE GENERATION
# =====================================================================
prompt = f"""
You are an executive CRE debt risk reporting compiler. Convert the structured surveillance payload below into an institutional credit memo. Be precise and concise.

{data_context_payload}

{cluster_summary}

{exceptions_payload}

RISK BUCKET REFERENCE:
| Score    | Bucket    | Action                                        |
|----------|-----------|-----------------------------------------------|
| 80–100   | CRITICAL  | Immediate asset management review             |
| 65–79    | ELEVATED  | Enhanced monitoring and remediation follow-up |
| 50–64    | WATCH     | Standard watchlist review                     |
| <50      | MONITOR   | Routine surveillance                          |

Output exactly these three sections:

## 📊 SURVEILLANCE MEMORANDUM MATRIX
Create a Markdown table: Property | Score | 30-Day Momentum | Velocity | Trend | Bucket | Z-Score | Anomaly

For each property:
- **PROPERTY**: [Address]
- **RISK BUCKET**: [Label] — [Action]
- **ESCALATION FLAGS**: [List any triggered rules or "None"]
- **EVENTS**:
  For each event: EVENT_TYPE | EVENT_CLASS | EVENT_STATUS | IMPACT_SCORE | DATE | DAYS OUTSTANDING | DESCRIPTION
- **COLLATERAL MONITORING COMMENTARY**: Objectively restate observed public records only. Do not infer borrower stress, default probability, or capital needs.

## 🔍 DATA QUALITY GATE
State exceptions exactly as provided. State they are excluded pending manual verification.
WATCHLIST CRITERIA: Properties scoring ≥{WATCHLIST_SCORE_THRESHOLD}/100. Max {MAX_PROPERTIES_PER_PROMPT} per run.
PORTFOLIO SYSTEMIC RISK INDEX: {systemic_index}/100

## 📢 DISCIPLINED SYNDICATION SUMMARY
Open with exactly:
"This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow. Based on {watchlist_count} monitored records scoring ≥{WATCHLIST_SCORE_THRESHOLD}/100, the review identified..."

Then:
- Name the highest-scoring asset, its bucket, and whether risk is driven by new signals, recurring conditions, or both
- Reference days outstanding for its top event
- State the portfolio systemic risk index ({systemic_index}/100) and what it signals
- Name any anomaly-flagged assets (Z-score ≥1.5)
- Name the dominant risk cluster if one event type dominates
- Close with one sentence on monitoring cadence

Guardrails:
- Use exact counts from payload only
- No non-standard banking phrases
- No repetition of compliance paragraph in body

End with:
"Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."

Hashtags: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    print("\n=====================================================================")
    print(f"📋 CRE SURVEILLANCE PLATFORM: VERSION 8.4 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📊 PORTFOLIO SYSTEMIC RISK INDEX: {systemic_index}/100")
    print(f"🏘️  RISK CLUSTERS: { {k: len(v) for k, v in cluster_map.items() if k != 'NONE'} }")
    print("=====================================================================")
    print(response.choices[0].message.content)
    print("=====================================================================")
except Exception as e:
    print(f"❌ Layer 9 Narrative Compiler Failure: {e}")

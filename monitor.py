import os
import math
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from groq import Groq

print("🚀 Launching Version 8.5: Institutional Credit Surveillance Engine...")

# =====================================================================
# 1. GOVERNANCE CALIBRATION
# =====================================================================
BOROUGH_BASELINES = {
    "MANHATTAN": 25, "BRONX": 35, "BROOKLYN": 30, "QUEENS": 25, "STATEN ISLAND": 20, "NYC": 25
}

RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35, "STRUCTURAL_INSTABILITY": 30, "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20, "LITIGATION_GENERAL": 15, "STANDARD_VIOLATION": 5,
    "REMEDIATION_EVENT": 0
}

# Risk buckets
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

# FIX 5: Tiered anomaly flags
def get_anomaly_flag(z):
    if z is None:
        return ""
    if z >= 2.5:
        return "⛔ EXTREME ANOMALY"
    elif z >= 2.0:
        return "🚨 SIGNIFICANT ANOMALY"
    elif z >= 1.5:
        return "⚠️ MILD ANOMALY"
    return ""

# Escalation rules
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

def evaluate_escalation(fingerprint):
    return [
        f"{r['id']}: {r['description']} → {r['action']}"
        for r in ESCALATION_RULES if r["condition"](fingerprint)
    ]

FRESH_WINDOW_DAYS      = 30
TOTAL_INGESTION_DAYS   = 90
WATCHLIST_THRESHOLD    = 50
MAX_PROPERTIES_PROMPT  = 10
MAX_EVENTS_PROPERTY    = 5
MAX_DESC_WORDS         = 10
MAX_CLUSTERS_SHOWN     = 5

cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str  = cutoff_date.strftime('%Y-%m-%d')

properties_db = {}
exceptions_log = []

# =====================================================================
# 2. UTILITY FUNCTIONS
# =====================================================================
def parse_nyc_date(date_str):
    if not date_str:
        return None
    clean = str(date_str).split("T")[0].replace("-", "").strip()
    for fmt in ["%Y%m%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue
    return None

def clean_address(item):
    num    = str(item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or item.get("street") or "").strip()
    return f"{num} {street}" if num and street else None

def compress_desc(text, max_words=MAX_DESC_WORDS):
    words = text.strip().split()
    return (" ".join(words[:max_words]) + "...") if len(words) > max_words else text.strip()

def classify_event(raw_desc, severity="", case_type=""):
    desc = raw_desc.upper()
    sev  = severity.upper()
    ct   = case_type.upper()
    if any(k in desc or k in ct for k in ["CODE COMPLIANCE","COMPLIED","CORRECTED","DISMISSED","IN CODE-COMPLIAN"]):
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
    return "STANDARD_VIOLATION", "Standard DOB Violation", "OPEN"

def deduplicate_events(events, max_ev=MAX_EVENTS_PROPERTY):
    seen, deduped = {}, []
    for ev in events:
        k = ev["event_type"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= 2:
            deduped.append(ev)
    return deduped[:max_ev]

# FIX 1: Statistically grounded z-score with explicit population definition
def compute_z_score(score, scores_list, label="borough"):
    """
    Borough-normalized z-score.
    Population: all scored properties within the same borough in this run.
    μ = borough mean score | σ = borough std dev
    """
    n = len(scores_list)
    if n < 2:
        return None, None, None
    mu  = sum(scores_list) / n
    std = math.sqrt(sum((s - mu) ** 2 for s in scores_list) / n)
    if std == 0:
        return 0.0, mu, std
    return round((score - mu) / std, 2), round(mu, 1), round(std, 1)

BORO_LOOKUP = {
    "1":"MANHATTAN","2":"BRONX","3":"BROOKLYN","4":"QUEENS","5":"STATEN ISLAND",
    "MN":"MANHATTAN","BX":"BRONX","BK":"BROOKLYN","QN":"QUEENS","SI":"STATEN ISLAND",
    "MANHATTAN":"MANHATTAN","BRONX":"BRONX","BROOKLYN":"BROOKLYN",
    "QUEENS":"QUEENS","STATEN ISLAND":"STATEN ISLAND"
}

# =====================================================================
# 3. DATA INGESTION
# =====================================================================
lit_url  = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

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
            exceptions_log.append(f"Litigation {rec_id}: Invalid timestamp.")
            continue
        addr = clean_address(record)
        if not addr:
            exceptions_log.append(f"Litigation {rec_id}: Incomplete address.")
            continue
        boro_code = str(record.get("boroid", "NYC"))
        boro_name = {"1":"MANHATTAN","2":"BRONX","3":"BROOKLYN","4":"QUEENS","5":"STATEN ISLAND"}.get(boro_code, "NYC")
        full_key  = f"{addr}, {boro_name}"
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}
        case_type = str(record.get("casetype", ""))
        et, ec, es = classify_event("", "", case_type)
        properties_db[full_key]["events"].append({
            "event_type": et, "event_class": ec, "event_status": es,
            "impact_score": RULE_SCORE_MATRIX.get(et, 5),
            "age_days": (datetime.now() - event_date).days,
            "event_date": event_date,
            "raw_desc": compress_desc(f"Litigation: {case_type}")
        })
except Exception as e:
    exceptions_log.append(f"Litigation API Failure: {e}")

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
            exceptions_log.append(f"Violation {rec_id}: Invalid timestamp.")
            continue
        addr = clean_address(record)
        if not addr:
            exceptions_log.append(f"Violation {rec_id}: Incomplete address.")
            continue
        boro_raw  = str(record.get("boro","") or record.get("borough","") or "").strip().upper()
        boro_name = next((v for k, v in BORO_LOOKUP.items() if k in boro_raw), "NYC")
        full_key  = f"{addr}, {boro_name}"
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}
        desc     = str(record.get("description", ""))
        severity = str(record.get("violation_category", ""))
        et, ec, es = classify_event(desc, severity)
        properties_db[full_key]["events"].append({
            "event_type": et, "event_class": ec, "event_status": es,
            "impact_score": RULE_SCORE_MATRIX.get(et, 5),
            "age_days": (datetime.now() - event_date).days,
            "event_date": event_date,
            "raw_desc": compress_desc(f"DOB: {desc}")
        })
except Exception as e:
    exceptions_log.append(f"DOB Violations API Failure: {e}")

if not properties_db or all(len(v["events"]) == 0 for v in properties_db.values()):
    print("⚠️ No qualifying observations detected.")
    for exc in exceptions_log[:5]: print(f"- {exc}")
    exit()

# =====================================================================
# 4. SCORE ENGINE — FIX 4: CLEAN SEPARATION OF SCORE / MOMENTUM / VELOCITY
# =====================================================================
# Score    = absolute lifetime risk state (capped 0-100)
# Momentum = sum of fresh (≤30d) event points only
# Velocity = acceleration = momentum delta vs prior 30d window (momentum - prior_momentum)

calculated_portfolio = []
borough_scores       = defaultdict(list)

for addr, asset in properties_db.items():
    boro     = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)

    lifetime_score   = baseline
    momentum_30d     = 0   # fresh window accumulator
    prior_momentum   = 0   # 31–60 day window (for velocity derivative)
    cat_counts       = {}
    structured_events = []

    for ev in sorted(asset["events"], key=lambda x: x["age_days"], reverse=True):
        cat            = ev["event_type"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        is_recurring   = cat_counts[cat] > 1
        amplifier      = 1.35 if is_recurring else 1.00
        base_points    = ev["impact_score"]
        points         = base_points * amplifier
        age            = ev["age_days"]

        if age <= FRESH_WINDOW_DAYS:
            momentum_30d  += points
            lifetime_score += points
            lifecycle = "Recurring Unresolved Condition" if is_recurring else "NEW_EVENT"
        elif age <= 60:
            prior_momentum += points
            lifetime_score += points * 0.70
            lifecycle = "Recurring Background Condition" if is_recurring else "PERSISTENT_BACKGROUND"
        else:
            lifetime_score += points * 0.50
            lifecycle = "Recurring Background Condition" if is_recurring else "PERSISTENT_BACKGROUND"

        structured_events.append({
            "event_type":      cat,
            "event_class":     lifecycle,
            "event_status":    ev["event_status"],
            "impact_score":    int(points),
            "event_date":      ev["event_date"].strftime("%m/%d/%Y") if ev.get("event_date") else "Unknown",
            "days_outstanding": age,
            "description":     ev["raw_desc"],
            "is_fresh":        age <= FRESH_WINDOW_DAYS
        })

    current_score  = min(int(lifetime_score), 100)
    momentum_30d   = min(int(momentum_30d), 100)
    prior_momentum = min(int(prior_momentum), 100)

    # FIX 4: Velocity = derivative of momentum (acceleration of change)
    velocity = momentum_30d - prior_momentum

    # Trend uses velocity (true acceleration)
    if velocity >= 30:
        trend = "▲▲ Rapid Acceleration"
    elif velocity >= 10:
        trend = "▲ Accelerating"
    elif velocity > 0:
        trend = "▲ Mild Acceleration"
    elif velocity == 0:
        trend = "→ Stable"
    else:
        trend = "▼ Decelerating"

    # FIX 6: Property fingerprint
    fingerprint = {
        "fire_events":       sum(1 for e in structured_events if e["event_type"] == "FIRE_DAMAGE"),
        "structural_events": sum(1 for e in structured_events if e["event_type"] == "STRUCTURAL_INSTABILITY"),
        "fresh_event_count": sum(1 for e in structured_events if e["is_fresh"]),
        "recurrence_index":  sum(1 for e in structured_events if "Recurring" in e["event_class"]),
        "recency_score":     momentum_30d,
        "lifetime_stress":   current_score,
        "dominant_type":     max(cat_counts, key=cat_counts.get) if cat_counts else "NONE"
    }

    bucket_label, bucket_action = get_risk_bucket(current_score)
    escalation_flags            = evaluate_escalation(fingerprint)
    compressed_events           = deduplicate_events(structured_events)

    # FIX 7: Causal attribution — top 3 drivers
    driver_scores = {}
    for ev in structured_events:
        t = ev["event_type"]
        driver_scores[t] = driver_scores.get(t, 0) + ev["impact_score"]
    top_drivers = sorted(driver_scores.items(), key=lambda x: x[1], reverse=True)[:3]
    causal_attribution = [
        f"{i+1}. {dtype} (cumulative impact: +{score} pts)"
        for i, (dtype, score) in enumerate(top_drivers)
    ]

    result = {
        "address":            addr,
        "boro":               boro,
        "current":            current_score,
        "momentum_30d":       momentum_30d,
        "prior_momentum":     prior_momentum,
        "velocity":           velocity,
        "trend":              trend,
        "bucket_label":       bucket_label,
        "bucket_action":      bucket_action,
        "fingerprint":        fingerprint,
        "escalation_flags":   escalation_flags,
        "causal_attribution": causal_attribution,
        "events":             compressed_events,
        "driver_scores":      driver_scores
    }
    calculated_portfolio.append(result)
    borough_scores[boro].append(current_score)

# =====================================================================
# 5. FIX 1: BOROUGH-NORMALIZED Z-SCORE (STATISTICALLY GROUNDED)
# =====================================================================
for asset in calculated_portfolio:
    scores         = borough_scores[asset["boro"]]
    z, mu, sigma   = compute_z_score(asset["current"], scores)
    asset["z_score"] = z
    asset["z_mu"]    = mu
    asset["z_sigma"] = sigma
    asset["anomaly"] = get_anomaly_flag(z)  # FIX 5: Tiered anomaly

# =====================================================================
# 6. FIX 3: WEIGHTED CLUSTER EXPOSURE (not raw counts)
# =====================================================================
cluster_exposure = defaultdict(float)
recency_weight   = lambda age: 1.0 if age <= FRESH_WINDOW_DAYS else 0.7 if age <= 60 else 0.5

for asset in calculated_portfolio:
    for ev in asset["events"]:
        w = recency_weight(ev["days_outstanding"])
        cluster_exposure[ev["event_type"]] += ev["impact_score"] * w

top_clusters = sorted(cluster_exposure.items(), key=lambda x: x[1], reverse=True)[:MAX_CLUSTERS_SHOWN]

# =====================================================================
# 7. WATCHLIST FILTER
# =====================================================================
active_watchlist = sorted(
    [a for a in calculated_portfolio if a["current"] >= WATCHLIST_THRESHOLD],
    key=lambda x: x["current"], reverse=True
)[:MAX_PROPERTIES_PROMPT]
watchlist_count = len(active_watchlist)

# =====================================================================
# 8. FIX 2: PORTFOLIO SYSTEMIC RISK INDEX DECOMPOSITION
# =====================================================================
if active_watchlist:
    total_score = sum(a["current"] for a in active_watchlist)
    systemic_index = round(total_score / watchlist_count, 1)

    fire_contrib       = round(sum(a["driver_scores"].get("FIRE_DAMAGE", 0)         for a in active_watchlist) / max(total_score, 1) * 100, 1)
    structural_contrib = round(sum(a["driver_scores"].get("STRUCTURAL_INSTABILITY", 0) for a in active_watchlist) / max(total_score, 1) * 100, 1)
    recency_contrib    = round(sum(a["momentum_30d"]                                  for a in active_watchlist) / max(total_score, 1) * 100, 1)
    recurrence_contrib = round(sum(a["fingerprint"]["recurrence_index"]               for a in active_watchlist) / max(watchlist_count, 1) * 10, 1)
    recurrence_contrib = min(recurrence_contrib, 100 - fire_contrib - structural_contrib - recency_contrib)

    systemic_decomposition = (
        f"  Structural Risk Contribution : {structural_contrib}%\n"
        f"  Fire Risk Concentration      : {fire_contrib}%\n"
        f"  Recency Pressure (30D)       : {recency_contrib}%\n"
        f"  Recurrence Density           : {recurrence_contrib}%"
    )
else:
    systemic_index        = 0.0
    systemic_decomposition = "  No active watchlist properties."

# =====================================================================
# 9. TOKEN-SAFE PROMPT ASSEMBLY (FIX 8)
# =====================================================================
data_payload = (
    f"WATCHLIST_COUNT: {watchlist_count} | "
    f"SYSTEMIC_RISK_INDEX: {systemic_index}/100\n"
    f"SYSTEMIC DECOMPOSITION:\n{systemic_decomposition}\n\n"
)

for asset in active_watchlist:
    fp = asset["fingerprint"]
    z_context = (
        f"Z={asset['z_score']} (μ={asset['z_mu']}, σ={asset['z_sigma']}, "
        f"pop=all {asset['boro']} properties this run)"
        if asset["z_score"] is not None else "Z=N/A (insufficient borough sample)"
    )
    data_payload += (
        f"PROPERTY: {asset['address']} ({asset['boro']})\n"
        f"  Score={asset['current']}/100 | Momentum(30D)={asset['momentum_30d']} | "
        f"PriorMomentum(31-60D)={asset['prior_momentum']} | "
        f"Velocity(Accel)={asset['velocity']:+d} | Trend={asset['trend']}\n"
        f"  BUCKET: {asset['bucket_label']} — {asset['bucket_action']}\n"
        f"  Z-SCORE: {z_context} | {asset['anomaly']}\n"
        f"  FINGERPRINT: Fire={fp['fire_events']} Structural={fp['structural_events']} "
        f"Fresh={fp['fresh_event_count']} Recurrence={fp['recurrence_index']} "
        f"Dominant={fp['dominant_type']}\n"
    )
    if asset["escalation_flags"]:
        data_payload += "  ESCALATION:\n" + "".join(f"    ⚠️ {f}\n" for f in asset["escalation_flags"])
    data_payload += "  CAUSAL ATTRIBUTION:\n" + "".join(f"    {d}\n" for d in asset["causal_attribution"])
    data_payload += "  EVENTS:\n"
    for ev in asset["events"]:
        data_payload += (
            f"    TYPE:{ev['event_type']} CLASS:{ev['event_class']} "
            f"STATUS:{ev['event_status']} IMPACT:+{ev['impact_score']} "
            f"DATE:{ev['event_date']} DAYS:{ev['days_outstanding']} "
            f"DESC:{ev['description']}\n"
        )
    data_payload += "\n"

cluster_payload = "WEIGHTED CLUSTER EXPOSURE (score × recency weight):\n"
for ctype, exposure in top_clusters:
    cluster_payload += f"  {ctype}: {round(exposure, 1)}\n"

exceptions_payload = "DATA QUALITY EXCLUSIONS:\n"
exceptions_payload += "\n".join(f"  - {e}" for e in exceptions_log[:3]) if exceptions_log else "  - None"

# =====================================================================
# 10. LLM NARRATIVE — FIX 6: TIGHT SYNDICATION SUMMARY
# =====================================================================
prompt = f"""
You are an institutional CRE credit surveillance compiler. Convert the payload into a precise credit memo. No repetition. No padding.

{data_payload}
{cluster_payload}
{exceptions_payload}

RISK BUCKET REFERENCE:
80-100=CRITICAL | 65-79=ELEVATED | 50-64=WATCH | <50=MONITOR

Output exactly these three sections:

## 📊 SURVEILLANCE MEMORANDUM MATRIX
Markdown table: Property | Score | Momentum(30D) | Prior Momentum(31-60D) | Velocity | Trend | Bucket | Z-Score | Anomaly

For each property:
- **PROPERTY**: [Address] ([Borough])
- **RISK BUCKET**: [Label] — [Action]
- **ESCALATION FLAGS**: [Triggered rules or "None"]
- **CAUSAL ATTRIBUTION**: [List top drivers exactly as provided]
- **EVENTS**: For each: TYPE | CLASS | STATUS | IMPACT | DATE | DAYS OUTSTANDING | DESC
- **COLLATERAL MONITORING COMMENTARY**: One paragraph. Restate observed records only. No inference of borrower stress, default probability, or capital needs.

## 🔍 DATA QUALITY GATE
- Exceptions exactly as provided, excluded pending manual verification
- WATCHLIST CRITERIA: Score ≥{WATCHLIST_THRESHOLD}/100 | Max {MAX_PROPERTIES_PROMPT} properties
- SYSTEMIC RISK INDEX: {systemic_index}/100 with decomposition as provided
- Z-SCORE BASIS: Borough-normalized | μ and σ derived from all scored properties in same borough this run

## 📢 DISCIPLINED SYNDICATION SUMMARY
Exactly 5 bullet points. No repetition of table data. No re-explaining metrics.
Open with: "This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow. Based on {watchlist_count} records scoring ≥{WATCHLIST_THRESHOLD}/100, the review identified..."

Bullets must cover:
1. Highest-scoring asset: name, bucket, primary causal driver only
2. Velocity signal: which asset is accelerating fastest and what that means for monitoring cadence
3. Portfolio systemic risk index headline and its dominant decomposition component
4. Anomaly flags: name any z-score anomalies with their tier (mild/significant/extreme)
5. Dominant weighted cluster and what it signals for collateral exposure concentration

Guardrails:
- Exact counts only from payload
- No non-standard banking phrases
- No compliance paragraph in body

End with exactly:
"Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."

Hashtags: #CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

# =====================================================================
# 11. OUTPUT
# =====================================================================
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
try:
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0
    )
    print("\n" + "="*70)
    print(f"📋 CRE SURVEILLANCE PLATFORM v8.5 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📊 SYSTEMIC RISK INDEX : {systemic_index}/100")
    print(f"📐 DECOMPOSITION       :\n{systemic_decomposition}")
    print(f"🏘️  TOP WEIGHTED CLUSTERS:")
    for ct, exp in top_clusters:
        print(f"   {ct}: {round(exp,1)}")
    print("="*70)
    print(response.choices[0].message.content)
    print("="*70)
except Exception as e:
    print(f"❌ Narrative Compiler Failure: {e}")

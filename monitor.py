import os
import math
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from groq import Groq

print("🚀 Launching Version 8.6: Institutional Credit Surveillance Engine...")

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

# Institutional risk buckets
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

# Tiered anomaly flags
def get_anomaly_flag(z):
    if z is None:   return ""
    if z >= 2.5:    return "⛔ EXTREME ANOMALY"
    elif z >= 2.0:  return "🚨 SIGNIFICANT ANOMALY"
    elif z >= 1.5:  return "⚠️ MILD ANOMALY"
    return ""

# Escalation rules engine
ESCALATION_RULES = [
    {
        "id": "RULE_FIRE_STRUCTURAL_COMBO",
        "description": "Fire and structural instability both detected",
        "condition": lambda fp: fp["fire_events"] > 0 and fp["structural_events"] > 0,
        "action": "AUTO ESCALATE → IMMEDIATE REVIEW"
    },
    {
        "id": "RULE_RAPID_FRESH_CLUSTER",
        "description": "Two or more unresolved events within 30 days",
        "condition": lambda fp: fp["fresh_event_count"] >= 2,
        "action": "ESCALATE TIER → ENHANCED MONITORING"
    },
    {
        "id": "RULE_OPERATIONAL_DECAY",
        "description": "Recurring condition flagged three or more times",
        "condition": lambda fp: fp["recurrence_index"] >= 3,
        "action": "FLAG → OPERATIONAL DECAY PATTERN"
    },
]

def evaluate_escalation(fingerprint):
    return [
        f"{r['id']}: {r['description']} → {r['action']}"
        for r in ESCALATION_RULES if r["condition"](fingerprint)
    ]

# Full event description map (v8.3 strength)
EVENT_DESCRIPTION_MAP = {
    "REMEDY: SEAL ALL FIRE DAM":   "Remedy – Seal all fire damage and unsafe conditions",
    "STRUCTURE RENDERED NON-CO":   "Structure rendered non-compliant",
    "REQUESTING A STRUCTURAL R":   "Requesting a structural review",
    "REQUESTING A STRUCTURAL S":   "Requesting a structural survey",
    "OBSERVED SWS AT THE FRONT":   "Observed sidewalk shed required at front of building",
    "FACADE":                      "Facade condition requiring inspection or repair",
    "COLLAPSE":                    "Collapse risk flagged by inspector",
    "HAZARDOUS":                   "Hazardous condition – Class 1 violation",
    "FIRE":                        "Fire damage or fire safety condition",
    "DEMOLISH":                    "Full or partial demolition order issued",
    "INSTALL APPROXIMA":           "Install approximate shoring or stabilization",
    "ENGINEERS REPORT":            "Engineer's report requested by DOB inspector",
    "LINEAR":                      "Erect linear barrier or sidewalk protection",
}

def expand_description(raw_desc):
    upper = raw_desc.upper()
    for key, expanded in EVENT_DESCRIPTION_MAP.items():
        if key in upper:
            return expanded
    return raw_desc.strip().title()

FRESH_WINDOW_DAYS     = 30
TOTAL_INGESTION_DAYS  = 90
WATCHLIST_THRESHOLD   = 50
MAX_PROPERTIES_PROMPT = 10
MAX_EVENTS_PROPERTY   = 5
MAX_CLUSTERS_SHOWN    = 5

cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str  = cutoff_date.strftime('%Y-%m-%d')

properties_db = {}
exceptions_log = []

BORO_LOOKUP = {
    "1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND",
    "MN": "MANHATTAN", "BX": "BRONX", "BK": "BROOKLYN", "QN": "QUEENS", "SI": "STATEN ISLAND",
    "MANHATTAN": "MANHATTAN", "BRONX": "BRONX", "BROOKLYN": "BROOKLYN",
    "QUEENS": "QUEENS", "STATEN ISLAND": "STATEN ISLAND"
}

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
    street = str(item.get("street_name")  or item.get("streetname")    or item.get("street")   or "").strip()
    return f"{num} {street}" if num and street else None

def classify_event(raw_desc, severity="", case_type=""):
    desc = raw_desc.upper()
    sev  = severity.upper()
    ct   = case_type.upper()
    if any(k in desc or k in ct for k in ["CODE COMPLIANCE","COMPLIED","CORRECTED","DISMISSED","IN CODE-COMPLIAN"]):
        return "REMEDIATION_EVENT", "Remediation Recorded", "REMEDIATED"
    elif "FIRE" in desc:
        return "FIRE_DAMAGE",           "Fire Damage or Safety Condition",     "OPEN"
    elif "FACADE" in desc or "COLLAPSE" in desc or "DEMOLISH" in desc:
        return "STRUCTURAL_INSTABILITY","Facade or Structural Instability",    "OPEN"
    elif "CLASS 1" in sev or "HAZARDOUS" in sev:
        return "HAZARDOUS_CLASS_1",     "Hazardous Condition Class 1",         "OPEN"
    elif "HARASSMENT" in ct:
        return "HARASSMENT_CLAIM",      "Tenant Harassment Claim",             "OPEN"
    elif ct and ct not in ["", "UNK"]:
        return "LITIGATION_GENERAL",    "General Housing Litigation",          "OPEN"
    return "STANDARD_VIOLATION",        "Standard DOB Violation",              "OPEN"

def deduplicate_events(events, max_ev=MAX_EVENTS_PROPERTY):
    seen, deduped = {}, []
    for ev in events:
        k = ev["event_type"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= 2:
            deduped.append(ev)
    return deduped[:max_ev]

def compute_z_score(score, scores_list):
    """Borough-normalized z-score. Population = all scored properties in same borough this run."""
    n = len(scores_list)
    if n < 2:
        return None, None, None
    mu  = sum(scores_list) / n
    std = math.sqrt(sum((s - mu) ** 2 for s in scores_list) / n)
    if std == 0:
        return 0.0, round(mu, 1), 0.0
    return round((score - mu) / std, 2), round(mu, 1), round(std, 1)

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
        boro_name = {"1":"MANHATTAN","2":"BRONX","3":"BROOKLYN","4":"QUEENS","5":"STATEN ISLAND"}.get(boro_code,"NYC")
        full_key  = f"{addr}, {boro_name}"
        if full_key not in properties_db:
            properties_db[full_key] = {"boro": boro_name, "events": []}
        case_type      = str(record.get("casetype", ""))
        et, ec, es     = classify_event("", "", case_type)
        readable_desc  = expand_description(f"Litigation: {case_type}")
        properties_db[full_key]["events"].append({
            "event_type": et, "event_class": ec, "event_status": es,
            "impact_score": RULE_SCORE_MATRIX.get(et, 5),
            "age_days": (datetime.now() - event_date).days,
            "event_date": event_date,
            "readable_desc": readable_desc
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
        desc           = str(record.get("description", ""))
        severity       = str(record.get("violation_category", ""))
        et, ec, es     = classify_event(desc, severity)
        readable_desc  = expand_description(f"DOB: {desc}")
        properties_db[full_key]["events"].append({
            "event_type": et, "event_class": ec, "event_status": es,
            "impact_score": RULE_SCORE_MATRIX.get(et, 5),
            "age_days": (datetime.now() - event_date).days,
            "event_date": event_date,
            "readable_desc": readable_desc
        })
except Exception as e:
    exceptions_log.append(f"DOB Violations API Failure: {e}")

if not properties_db or all(len(v["events"]) == 0 for v in properties_db.values()):
    print("⚠️ No qualifying observations detected.")
    for exc in exceptions_log[:5]: print(f"- {exc}")
    exit()

# =====================================================================
# 4. SCORE ENGINE — CLEAN SEPARATION: SCORE / MOMENTUM / VELOCITY
# =====================================================================
# Score    = absolute lifetime risk state (capped 0–100)
# Momentum = fresh 30-day event accumulation only
# Velocity = acceleration = momentum(0-30D) minus prior momentum(31-60D)

calculated_portfolio = []
borough_scores       = defaultdict(list)

for addr, asset in properties_db.items():
    boro     = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)

    lifetime_score  = baseline
    momentum_30d    = 0
    prior_momentum  = 0
    cat_counts      = {}
    structured_events = []

    for ev in sorted(asset["events"], key=lambda x: x["age_days"], reverse=True):
        cat           = ev["event_type"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        is_recurring  = cat_counts[cat] > 1
        amplifier     = 1.35 if is_recurring else 1.00
        base_points   = ev["impact_score"]
        points        = base_points * amplifier
        age           = ev["age_days"]

        if age <= FRESH_WINDOW_DAYS:
            momentum_30d   += points
            lifetime_score += points
            lifecycle = "Recurring Unresolved Condition" if is_recurring else "New Event"
        elif age <= 60:
            prior_momentum += points
            lifetime_score += points * 0.70
            lifecycle = "Recurring Background Condition" if is_recurring else "Persistent Background"
        else:
            lifetime_score += points * 0.50
            lifecycle = "Recurring Background Condition" if is_recurring else "Persistent Background"

        structured_events.append({
            "event_type":       cat,
            "event_class":      lifecycle,
            "event_status":     ev["event_status"],
            "impact_score":     int(points),
            "event_date":       ev["event_date"].strftime("%m/%d/%Y") if ev.get("event_date") else "Unknown",
            "days_outstanding": age,
            "readable_desc":    ev["readable_desc"],
            "is_fresh":         age <= FRESH_WINDOW_DAYS
        })

    current_score   = min(int(lifetime_score), 100)
    momentum_30d    = min(int(momentum_30d), 100)
    prior_momentum  = min(int(prior_momentum), 100)
    velocity        = momentum_30d - prior_momentum

    if velocity >= 30:   trend = "▲▲ Rapid Acceleration"
    elif velocity >= 10: trend = "▲ Accelerating"
    elif velocity > 0:   trend = "▲ Mild Acceleration"
    elif velocity == 0:  trend = "→ Stable"
    else:                trend = "▼ Decelerating"

    # Property fingerprint
    fingerprint = {
        "fire_events":       sum(1 for e in structured_events if e["event_type"] == "FIRE_DAMAGE"),
        "structural_events": sum(1 for e in structured_events if e["event_type"] == "STRUCTURAL_INSTABILITY"),
        "fresh_event_count": sum(1 for e in structured_events if e["is_fresh"]),
        "recurrence_index":  sum(1 for e in structured_events if "Recurring" in e["event_class"]),
        "recency_score":     momentum_30d,
        "lifetime_stress":   current_score,
        "dominant_type":     max(cat_counts, key=cat_counts.get) if cat_counts else "NONE"
    }

    # Causal attribution — top 3 drivers by cumulative impact
    driver_scores = {}
    for ev in structured_events:
        t = ev["event_type"]
        driver_scores[t] = driver_scores.get(t, 0) + ev["impact_score"]
    top_drivers = sorted(driver_scores.items(), key=lambda x: x[1], reverse=True)[:3]
    causal_attribution = [
        f"{i+1}. {dtype.replace('_',' ').title()} — cumulative impact: +{score} pts"
        for i, (dtype, score) in enumerate(top_drivers)
    ]

    bucket_label, bucket_action = get_risk_bucket(current_score)
    escalation_flags            = evaluate_escalation(fingerprint)
    compressed_events           = deduplicate_events(structured_events)

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
# 5. BOROUGH-NORMALIZED Z-SCORE + TIERED ANOMALY
# =====================================================================
for asset in calculated_portfolio:
    scores       = borough_scores[asset["boro"]]
    z, mu, sigma = compute_z_score(asset["current"], scores)
    asset["z_score"] = z
    asset["z_mu"]    = mu
    asset["z_sigma"] = sigma
    asset["anomaly"] = get_anomaly_flag(z)

# =====================================================================
# 6. WEIGHTED CLUSTER EXPOSURE
# =====================================================================
recency_weight   = lambda age: 1.0 if age <= FRESH_WINDOW_DAYS else 0.7 if age <= 60 else 0.5
cluster_exposure = defaultdict(float)
for asset in calculated_portfolio:
    for ev in asset["events"]:
        cluster_exposure[ev["event_type"]] += ev["impact_score"] * recency_weight(ev["days_outstanding"])
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
# 8. PORTFOLIO SYSTEMIC RISK INDEX DECOMPOSITION
# =====================================================================
if active_watchlist:
    total_score        = sum(a["current"] for a in active_watchlist)
    systemic_index     = round(total_score / watchlist_count, 1)
    fire_contrib       = round(sum(a["driver_scores"].get("FIRE_DAMAGE",0)            for a in active_watchlist) / max(total_score,1) * 100, 1)
    structural_contrib = round(sum(a["driver_scores"].get("STRUCTURAL_INSTABILITY",0) for a in active_watchlist) / max(total_score,1) * 100, 1)
    recency_contrib    = round(sum(a["momentum_30d"]                                   for a in active_watchlist) / max(total_score,1) * 100, 1)
    recurrence_raw     = round(sum(a["fingerprint"]["recurrence_index"]                for a in active_watchlist) / max(watchlist_count,1) * 10, 1)
    recurrence_contrib = min(recurrence_raw, max(0, 100 - fire_contrib - structural_contrib - recency_contrib))
    systemic_decomp = (
        f"  Structural Risk Contribution : {structural_contrib}%\n"
        f"  Fire Risk Concentration      : {fire_contrib}%\n"
        f"  Recency Pressure (30D)       : {recency_contrib}%\n"
        f"  Recurrence Density           : {recurrence_contrib}%"
    )
else:
    systemic_index  = 0.0
    systemic_decomp = "  No active watchlist properties."

# =====================================================================
# 9. PROMPT ASSEMBLY — FULL PROPERTY DETAIL + TOKEN DISCIPLINE
# =====================================================================
data_payload = (
    f"WATCHLIST_COUNT: {watchlist_count} | SYSTEMIC_RISK_INDEX: {systemic_index}/100\n"
    f"SYSTEMIC DECOMPOSITION:\n{systemic_decomp}\n\n"
)

for asset in active_watchlist:
    fp = asset["fingerprint"]
    z_context = (
        f"Z={asset['z_score']} (μ={asset['z_mu']}, σ={asset['z_sigma']}, "
        f"population=all {asset['boro']} properties this run)"
        if asset["z_score"] is not None
        else "Z=N/A (insufficient borough sample)"
    )
    data_payload += (
        f"PROPERTY: {asset['address']} | BOROUGH: {asset['boro']}\n"
        f"  COLLATERAL RISK SCORE : {asset['current']}/100\n"
        f"  MOMENTUM (30D)        : {asset['momentum_30d']} pts\n"
        f"  PRIOR MOMENTUM (31-60D): {asset['prior_momentum']} pts\n"
        f"  VELOCITY (ACCEL)      : {asset['velocity']:+d} pts\n"
        f"  TREND                 : {asset['trend']}\n"
        f"  RISK BUCKET           : {asset['bucket_label']} — {asset['bucket_action']}\n"
        f"  Z-SCORE               : {z_context}\n"
        f"  ANOMALY               : {asset['anomaly'] if asset['anomaly'] else 'None'}\n"
        f"  FINGERPRINT           : Fire={fp['fire_events']} | Structural={fp['structural_events']} | "
        f"Fresh Events={fp['fresh_event_count']} | Recurrence Index={fp['recurrence_index']} | "
        f"Dominant Type={fp['dominant_type']}\n"
    )
    if asset["escalation_flags"]:
        data_payload += "  ESCALATION FLAGS:\n"
        for flag in asset["escalation_flags"]:
            data_payload += f"    ⚠️  {flag}\n"
    else:
        data_payload += "  ESCALATION FLAGS: None\n"

    data_payload += "  CAUSAL ATTRIBUTION (TOP DRIVERS):\n"
    for d in asset["causal_attribution"]:
        data_payload += f"    {d}\n"

    data_payload += f"  OBSERVED PUBLIC RECORD EVENTS ({len(asset['events'])} recorded):\n"
    for ev in asset["events"]:
        data_payload += (
            f"    EVENT TYPE       : {ev['event_type'].replace('_',' ').title()}\n"
            f"    EVENT CLASS      : {ev['event_class']}\n"
            f"    STATUS           : {ev['event_status']}\n"
            f"    DESCRIPTION      : {ev['readable_desc']}\n"
            f"    IMPACT SCORE     : +{ev['impact_score']} pts\n"
            f"    EVENT DATE       : {ev['event_date']}\n"
            f"    DAYS OUTSTANDING : {ev['days_outstanding']}\n"
            f"    ---\n"
        )
    data_payload += "\n"

cluster_payload = "WEIGHTED CLUSTER EXPOSURE (impact score × recency weight):\n"
for ctype, exposure in top_clusters:
    cluster_payload += f"  {ctype.replace('_',' ').title()}: {round(exposure,1)}\n"

exceptions_payload = "DATA QUALITY EXCLUSIONS:\n"
exceptions_payload += "\n".join(f"  - {e}" for e in exceptions_log[:3]) if exceptions_log else "  - None"

# =====================================================================
# 10. LLM NARRATIVE GENERATION
# =====================================================================
prompt = f"""
You are an institutional CRE credit surveillance compiler producing a formal lender memo.
Convert the structured payload below into a clean, precise, audit-ready credit report.
No padding. No repetition. No inference beyond observed public records.

{data_payload}
{cluster_payload}
{exceptions_payload}

RISK BUCKET REFERENCE:
| Score  | Bucket   | Recommended Action                              |
|--------|----------|-------------------------------------------------|
| 80–100 | CRITICAL | Immediate asset management review               |
| 65–79  | ELEVATED | Enhanced monitoring and remediation follow-up   |
| 50–64  | WATCH    | Standard watchlist review                       |
| <50    | MONITOR  | Routine surveillance                            |

===== OUTPUT FORMAT =====

## 📊 CREDIT SURVEILLANCE MEMORANDUM — {datetime.now().strftime('%B %d, %Y')}

Open with a one-line portfolio headline:
"Surveillance run completed: [watchlist_count] properties flagged at or above {WATCHLIST_THRESHOLD}/100. Portfolio Systemic Risk Index: {systemic_index}/100."

Then produce a Markdown matrix table with these columns:
Property Address | Borough | Collateral Risk Score | Momentum (30D) | Velocity | Trend | Risk Bucket | Z-Score | Anomaly Flag

Then for each property produce a structured property card using exactly this format:

---
### [ADDRESS] | [BOROUGH]
**Risk Bucket:** [BUCKET LABEL] — [ACTION]
**Collateral Risk Score:** [SCORE]/100 | **Momentum (30D):** [MOMENTUM] pts | **Velocity:** [VELOCITY:+d] pts | **Trend:** [TREND]
**Z-Score:** [Z] (μ=[MU], σ=[SIGMA], population: all [BOROUGH] properties this run) | **Anomaly:** [FLAG or None]

**Escalation Flags:**
[List each triggered rule or state "No escalation rules triggered"]

**Causal Attribution — Top Risk Drivers:**
[List each driver exactly as provided]

**Observed Public Record Events:**
| # | Event Type | Event Class | Status | Description | Impact | Event Date | Days Outstanding |
|---|-----------|-------------|--------|-------------|--------|------------|-----------------|
[One row per event]

**Collateral Monitoring Commentary:**
[One paragraph. Restate only what the public records show. Reference event type, days outstanding, and recurrence status. Do not infer borrower financial condition, default probability, capital needs, or tenant displacement.]

---

## 🔍 DATA QUALITY GATE & METHODOLOGY

**Watchlist Criteria:** Properties scoring ≥{WATCHLIST_THRESHOLD}/100 | Maximum {MAX_PROPERTIES_PROMPT} properties per run
**Portfolio Systemic Risk Index:** {systemic_index}/100
**Decomposition:**
{systemic_decomp}

**Weighted Cluster Exposure:**
[List top clusters from payload]

**Z-Score Methodology:** Borough-normalized. Population = all scored properties within the same borough in this surveillance run. μ and σ computed fresh each run.

**Anomaly Tiers:** ⚠️ Mild (Z ≥ 1.5) | 🚨 Significant (Z ≥ 2.0) | ⛔ Extreme (Z ≥ 2.5)

**Data Quality Exclusions:**
[List exceptions exactly as provided. State each is excluded pending manual file verification.]

## 📢 SYNDICATION SUMMARY

Open with exactly:
"This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow. Based on {watchlist_count} monitored records scoring ≥{WATCHLIST_THRESHOLD}/100, the review identified..."

Then exactly 5 bullets — no repetition of table data:
1. Highest-scoring asset: name, bucket, and primary causal driver only
2. Velocity signal: which asset shows fastest acceleration and what that implies for monitoring cadence
3. Portfolio systemic risk index headline and its single dominant decomposition component
4. Anomaly flags: name any z-score anomalies with tier label
5. Dominant weighted cluster and what it signals for collateral exposure concentration

Guardrails:
- Exact counts from payload only
- No non-standard banking phrases
- No compliance paragraph in the body

Close with exactly:
"Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."

#CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
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
    print(f"📋 CRE SURVEILLANCE PLATFORM v8.6 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"📊 SYSTEMIC RISK INDEX  : {systemic_index}/100")
    print(f"📐 DECOMPOSITION        :\n{systemic_decomp}")
    print(f"🏘️  TOP WEIGHTED CLUSTERS:")
    for ct, exp in top_clusters:
        print(f"   {ct.replace('_',' ').title()}: {round(exp,1)}")
    print(f"📋 WATCHLIST COUNT      : {watchlist_count} properties")
    print("="*70 + "\n")
    print(response.choices[0].message.content)
    print("\n" + "="*70)
except Exception as e:
    print(f"❌ Narrative Compiler Failure: {e}")

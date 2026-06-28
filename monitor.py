import os
import math
import time
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from groq import Groq

print("🚀 Launching Version 8.7: Institutional Credit Surveillance Engine...")

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

def get_anomaly_flag(z):
    if z is None:  return ""
    if z >= 2.5:   return "⛔ EXTREME ANOMALY"
    elif z >= 2.0: return "🚨 SIGNIFICANT ANOMALY"
    elif z >= 1.5: return "⚠️ MILD ANOMALY"
    return ""

# FIX 1: Normalized escalation rules — all thresholds evaluated against
# raw event counts from structured_events, not fingerprint proxies,
# ensuring consistent application regardless of borough or batch position.
def evaluate_escalation(structured_events):
    fire_count       = sum(1 for e in structured_events if e["event_type"] == "FIRE_DAMAGE"            and e["event_status"] == "OPEN")
    structural_count = sum(1 for e in structured_events if e["event_type"] == "STRUCTURAL_INSTABILITY" and e["event_status"] == "OPEN")
    fresh_open_count = sum(1 for e in structured_events if e["is_fresh"]                               and e["event_status"] == "OPEN")
    recurring_count  = sum(1 for e in structured_events if "Recurring" in e["event_class"])

    flags = []
    if fire_count > 0 and structural_count > 0:
        flags.append("RULE_FIRE_STRUCTURAL_COMBO: Fire and structural instability both detected → AUTO ESCALATE → IMMEDIATE REVIEW")
    if fresh_open_count >= 2:
        flags.append("RULE_RAPID_FRESH_CLUSTER: Two or more open unresolved events within 30 days → ESCALATE TIER → ENHANCED MONITORING")
    if recurring_count >= 3:
        flags.append("RULE_OPERATIONAL_DECAY: Recurring condition flagged three or more times → FLAG → OPERATIONAL DECAY PATTERN")
    return flags

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
BATCH_SIZE            = 3

cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str  = cutoff_date.strftime('%Y-%m-%d')

properties_db  = {}
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
        return "FIRE_DAMAGE",            "Fire Damage or Safety Condition",   "OPEN"
    elif "FACADE" in desc or "COLLAPSE" in desc or "DEMOLISH" in desc:
        return "STRUCTURAL_INSTABILITY", "Facade or Structural Instability",  "OPEN"
    elif "CLASS 1" in sev or "HAZARDOUS" in sev:
        return "HAZARDOUS_CLASS_1",      "Hazardous Condition Class 1",       "OPEN"
    elif "HARASSMENT" in ct:
        return "HARASSMENT_CLAIM",       "Tenant Harassment Claim",           "OPEN"
    elif ct and ct not in ["", "UNK"]:
        return "LITIGATION_GENERAL",     "General Housing Litigation",        "OPEN"
    return "STANDARD_VIOLATION",         "Standard DOB Violation",            "OPEN"

def deduplicate_events(events, max_ev=MAX_EVENTS_PROPERTY):
    seen, deduped = {}, []
    for ev in events:
        k = ev["event_type"]
        seen[k] = seen.get(k, 0) + 1
        if seen[k] <= 2:
            deduped.append(ev)
    return deduped[:max_ev]

# FIX 4: Z-score computed once across full portfolio AFTER all scoring is complete.
# Population definition:
#   - Cross-sectional: all properties scored in this run within the same borough
#   - NOT per-batch: borough_scores dict is fully populated before any z-score is computed
#   - Limitation explicitly flagged in methodology: snapshot-based, not longitudinal
def compute_z_score(score, scores_list):
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
        case_type     = str(record.get("casetype", ""))
        et, ec, es    = classify_event("", "", case_type)
        readable_desc = expand_description(f"Litigation: {case_type}")
        properties_db[full_key]["events"].append({
            "event_type": et, "event_class": ec, "event_status": es,
            "impact_score": RULE_SCORE_MATRIX.get(et, 5),
            "age_days": (datetime.now() - event_date).days,
            "event_date": event_date, "readable_desc": readable_desc
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
        desc          = str(record.get("description", ""))
        severity      = str(record.get("violation_category", ""))
        et, ec, es    = classify_event(desc, severity)
        readable_desc = expand_description(f"DOB: {desc}")
        properties_db[full_key]["events"].append({
            "event_type": et, "event_class": ec, "event_status": es,
            "impact_score": RULE_SCORE_MATRIX.get(et, 5),
            "age_days": (datetime.now() - event_date).days,
            "event_date": event_date, "readable_desc": readable_desc
        })
except Exception as e:
    exceptions_log.append(f"DOB Violations API Failure: {e}")

if not properties_db or all(len(v["events"]) == 0 for v in properties_db.values()):
    print("⚠️ No qualifying observations detected.")
    for exc in exceptions_log[:5]: print(f"- {exc}")
    exit()

# =====================================================================
# 4. SCORE ENGINE — FULL PORTFOLIO PASS (z-scores computed after)
# =====================================================================
calculated_portfolio = []
borough_scores       = defaultdict(list)  # FIX 4: populated in full before z-score pass

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
        points        = ev["impact_score"] * amplifier
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

    current_score  = min(int(lifetime_score), 100)
    momentum_30d   = min(int(momentum_30d), 100)
    prior_momentum = min(int(prior_momentum), 100)
    velocity       = momentum_30d - prior_momentum

    if velocity >= 30:   trend = "▲▲ Rapid Acceleration"
    elif velocity >= 10: trend = "▲ Accelerating"
    elif velocity > 0:   trend = "▲ Mild Acceleration"
    elif velocity == 0:  trend = "→ Stable"
    else:                trend = "▼ Decelerating"

    # FIX 1: Escalation evaluated directly from structured_events — consistent across all boroughs
    escalation_flags = evaluate_escalation(structured_events)

    # FIX 2: Causal attribution built as a clean list — one entry per line, no concatenation
    driver_scores = {}
    for ev in structured_events:
        t = ev["event_type"]
        driver_scores[t] = driver_scores.get(t, 0) + ev["impact_score"]
    top_drivers        = sorted(driver_scores.items(), key=lambda x: x[1], reverse=True)[:3]
    causal_attribution = []  # FIX 2: explicit list, never joined inline
    for i, (dtype, score) in enumerate(top_drivers):
        causal_attribution.append(f"{i+1}. {dtype.replace('_',' ').title()} — cumulative impact: +{score} pts")

    bucket_label, bucket_action = get_risk_bucket(current_score)
    compressed_events           = deduplicate_events(structured_events)

    fingerprint = {
        "fire_events":       sum(1 for e in structured_events if e["event_type"] == "FIRE_DAMAGE"),
        "structural_events": sum(1 for e in structured_events if e["event_type"] == "STRUCTURAL_INSTABILITY"),
        "fresh_event_count": sum(1 for e in structured_events if e["is_fresh"]),
        "recurrence_index":  sum(1 for e in structured_events if "Recurring" in e["event_class"]),
        "recency_score":     momentum_30d,
        "lifetime_stress":   current_score,
        "dominant_type":     max(cat_counts, key=cat_counts.get) if cat_counts else "NONE"
    }

    result = {
        "address": addr, "boro": boro,
        "current": current_score, "momentum_30d": momentum_30d,
        "prior_momentum": prior_momentum, "velocity": velocity, "trend": trend,
        "bucket_label": bucket_label, "bucket_action": bucket_action,
        "fingerprint": fingerprint, "escalation_flags": escalation_flags,
        "causal_attribution": causal_attribution,
        "events": compressed_events, "driver_scores": driver_scores
    }
    calculated_portfolio.append(result)
    borough_scores[boro].append(current_score)  # FIX 4: accumulate all scores first

# =====================================================================
# 5. FIX 4: Z-SCORE PASS — AFTER FULL PORTFOLIO IS SCORED
# Borough scores are now complete before any z-score is computed.
# =====================================================================
for asset in calculated_portfolio:
    scores       = borough_scores[asset["boro"]]
    z, mu, sigma = compute_z_score(asset["current"], scores)
    asset["z_score"] = z
    asset["z_mu"]    = mu
    asset["z_sigma"] = sigma
    asset["anomaly"] = get_anomaly_flag(z)

# =====================================================================
# =====================================================================
# 6. WEIGHTED CLUSTER EXPOSURE
# FIX 2: Computed across watchlist only so memo numbers match visible property cards
# FIX 3: Totals reflect post-deduplication exposure (max 2 events per type per property)
# =====================================================================
recency_weight = lambda age: 1.0 if age <= FRESH_WINDOW_DAYS else 0.7 if age <= 60 else 0.5

# =====================================================================
# 7. WATCHLIST
# =====================================================================
active_watchlist = sorted(
    [a for a in calculated_portfolio if a["current"] >= WATCHLIST_THRESHOLD],
    key=lambda x: x["current"], reverse=True
)[:MAX_PROPERTIES_PROMPT]
watchlist_count = len(active_watchlist)

# =====================================================================
# 8. SYSTEMIC RISK INDEX DECOMPOSITION
# =====================================================================
if active_watchlist:
    total_score    = sum(a["current"] for a in active_watchlist)
    systemic_index = round(total_score / watchlist_count, 1)

    # FIX 2: Cluster exposure scoped to watchlist only — not full portfolio
    watchlist_cluster_exposure = defaultdict(float)
    for a in active_watchlist:
        for ev in a["events"]:
            watchlist_cluster_exposure[ev["event_type"]] += (
                ev["impact_score"] * recency_weight(ev["days_outstanding"])
            )

    # FIX 1: These are independent ratio indicators, not additive decomposition.
    # Each is expressed as a share of total watchlist score for that signal type.
    # They are NOT mutually exclusive and will NOT sum to 100%.
    total_fire       = sum(a["driver_scores"].get("FIRE_DAMAGE", 0)            for a in active_watchlist)
    total_structural = sum(a["driver_scores"].get("STRUCTURAL_INSTABILITY", 0) for a in active_watchlist)
    total_momentum   = sum(a["momentum_30d"]                                    for a in active_watchlist)
    avg_recurrence   = sum(a["fingerprint"]["recurrence_index"]                 for a in active_watchlist) / max(watchlist_count, 1)

    fire_ratio       = round(total_fire       / max(total_score, 1) * 100, 1)
    structural_ratio = round(total_structural / max(total_score, 1) * 100, 1)
    recency_ratio    = round(total_momentum   / max(total_score, 1) * 100, 1)
    recurrence_avg   = round(avg_recurrence, 2)

    # FIX 1: Renamed to "Risk Component Indicators" with explicit methodology note
    systemic_decomp = (
        f"  NOTE: These are independent ratio indicators, not additive percentages.\n"
        f"  Each measures a specific signal as a share of total watchlist risk score.\n"
        f"  They are not mutually exclusive and will not sum to 100%.\n"
        f"  ─────────────────────────────────────────────────────\n"
        f"  Fire Risk Ratio           : {fire_ratio}% of total watchlist score\n"
        f"  Structural Risk Ratio     : {structural_ratio}% of total watchlist score\n"
        f"  Recency Pressure (30D)    : {recency_ratio}% of total watchlist score\n"
        f"  Avg Recurrence per Asset  : {recurrence_avg} recurring conditions"
    )
else:
    systemic_index              = 0.0
    systemic_decomp             = "  No active watchlist properties."
    watchlist_cluster_exposure  = defaultdict(float)

# =====================================================================
# 9. PROMPT ASSEMBLY
# =====================================================================
matrix_rows = []
for a in active_watchlist:
    z_str = f"{a['z_score']}" if a["z_score"] is not None else "N/A"
    matrix_rows.append(
        f"| {a['address']} | {a['boro']} | {a['current']}/100 | {a['momentum_30d']} pts "
        f"| {a['velocity']:+d} pts | {a['trend']} | {a['bucket_label']} | Z={z_str} | {a['anomaly'] or 'None'} |"
    )

# Use watchlist-scoped cluster exposure so memo numbers match property cards
top_clusters  = sorted(watchlist_cluster_exposure.items(), key=lambda x: x[1], reverse=True)[:MAX_CLUSTERS_SHOWN]
cluster_lines = (
    "  NOTE: Watchlist-scoped only. Totals reflect post-deduplication exposure (max 2 events per type per property).\n" +
    "\n".join(f"  {ct.replace('_',' ').title()}: {round(exp,1)}" for ct, exp in top_clusters)
)
exceptions_lines   = "\n".join(f"  - {e}" for e in exceptions_log[:3]) if exceptions_log else "  - None"
run_date_str       = datetime.now().strftime('%B %d, %Y')
run_datetime_str   = datetime.now().strftime('%Y-%m-%d %H:%M')

# FIX 4 & 6: Z-score methodology note — explicit snapshot limitation
z_methodology_note = (
    "Borough-normalized cross-sectional snapshot. "
    "Population = all properties scored within the same borough in this run. "
    "μ and σ computed once after full portfolio scoring pass — not per batch. "
    "⚠️ Limitation: This is a point-in-time distribution, not a longitudinal baseline. "
    "True credit-grade z-scores require multi-period historical population data."
)

prompt_1 = f"""
You are an institutional CRE credit surveillance compiler. Produce Sections 1 and 2 of a formal lender memo.
Be precise. No padding. No inference beyond observed public records.

RUN DATE: {run_date_str}
WATCHLIST COUNT: {watchlist_count}
SYSTEMIC RISK INDEX: {systemic_index}/100
DECOMPOSITION:
{systemic_decomp}

WEIGHTED CLUSTER EXPOSURE:
{cluster_lines}

RISK BUCKET REFERENCE:
| Score  | Bucket   | Action                                          |
|--------|----------|-------------------------------------------------|
| 80-100 | CRITICAL | Immediate asset management review               |
| 65-79  | ELEVATED | Enhanced monitoring and remediation follow-up   |
| 50-64  | WATCH    | Standard watchlist review                       |
| <50    | MONITOR  | Routine surveillance                            |

MATRIX DATA (reproduce exactly):
| Property Address | Borough | Collateral Risk Score | Momentum (30D) | Velocity | Trend | Risk Bucket | Z-Score | Anomaly Flag |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(matrix_rows)}

DATA QUALITY EXCLUSIONS:
{exceptions_lines}

===== OUTPUT FORMAT =====

## 📊 CREDIT SURVEILLANCE MEMORANDUM — {run_date_str}

"Surveillance run completed: {watchlist_count} properties flagged at or above {WATCHLIST_THRESHOLD}/100. Portfolio Systemic Risk Index: {systemic_index}/100."

[Reproduce matrix table exactly as provided]

## 🔍 DATA QUALITY GATE & METHODOLOGY

**Watchlist Criteria:** Properties scoring ≥{WATCHLIST_THRESHOLD}/100 | Max {MAX_PROPERTIES_PROMPT} per run
**Portfolio Systemic Risk Index:** {systemic_index}/100
**Decomposition:**
{systemic_decomp}

**Weighted Cluster Exposure:**
{cluster_lines}

**Z-Score Methodology:** {z_methodology_note}
**Anomaly Tiers:** ⚠️ Mild (Z ≥ 1.5) | 🚨 Significant (Z ≥ 2.0) | ⛔ Extreme (Z ≥ 2.5)

**Data Quality Exclusions:**
{exceptions_lines}
Each record above is excluded from all calculations pending manual file verification.

## 📢 SYNDICATION SUMMARY

Open with exactly:
"This week, I tracked how quickly operational risk can emerge across NYC multifamily assets using a public-record surveillance workflow. Based on {watchlist_count} monitored records scoring ≥{WATCHLIST_THRESHOLD}/100, the review identified..."

Exactly 5 bullets — no repetition of table data:
1. Highest-scoring asset: name, bucket, and primary causal driver only
2. Velocity signal: fastest-accelerating asset and monitoring cadence implication
3. Portfolio systemic risk index headline and dominant decomposition component
4. Anomaly flags: name z-score anomalies with tier label
5. Dominant weighted cluster and collateral exposure concentration signal

No credit opinions. No borrower inferences. Descriptive surveillance language only.

Close with exactly:
"Public records do not determine borrower liquidity, DSCR performance, or loan default probability. However, they can provide an early indication of collateral issues that may warrant additional diligence before they appear in standard reporting cycles."

#CREFinance #CREDebt #RiskManagement #CommercialRealEstate #Multifamily
"""

# FIX 2 + 3 + 5: Card template with strict commentary guardrails
# FIX 5: Commentary and Credit Opinion are now separate labeled layers
CARD_PROMPT_TEMPLATE = """
You are an institutional CRE credit surveillance compiler. Produce full property cards.
Strict rules:
- Reproduce all data exactly as provided. Do not recalculate or reformat numbers.
- Causal attribution: print each driver on its own numbered line. Never concatenate on one line.
- Collateral Monitoring Commentary: write 2-3 full prose sentences. State what public records show using natural language. Reference the event type by name, whether it is a new event or recurring condition, the date filed, and how many days it has been outstanding. Example: "A fire damage event was filed on 06/23/2026 and remains open after 1 day. A recurring unresolved fire damage condition was also recorded on the same date, indicating the condition has persisted across multiple observation periods." Do not use comma-separated lists. Do not imply credit advice, borrower obligation, financial consequence, or remediation urgency.
- Credit Opinion layer: state "NOT GENERATED — reserved for qualified credit officer review." Do not populate it.

PROPERTY DATA:
{batch_payload}

===== OUTPUT FORMAT FOR EACH PROPERTY =====

---
### [ADDRESS] | [BOROUGH]
**Risk Bucket:** [BUCKET LABEL] — [ACTION]
**Collateral Risk Score:** [SCORE]/100 | **Momentum (30D):** [MOMENTUM] pts | **Velocity:** [VELOCITY] pts | **Trend:** [TREND]
**Z-Score:** [Z-SCORE CONTEXT] | **Anomaly:** [FLAG or None]

**Escalation Flags:**
[List each triggered rule on its own line, or state "No escalation rules triggered"]

**Causal Attribution — Top Risk Drivers:**
[Print each numbered driver on its own line — never combine on one line]

**Observed Public Record Events:**
| # | Event Type | Event Class | Status | Description | Impact | Event Date | Days Outstanding |
|---|-----------|-------------|--------|-------------|--------|------------|-----------------|
[One row per event]

**Collateral Monitoring Commentary:**
[Descriptive only. State what public records show: event type, class, date filed, days outstanding, open/remediated status. Do not state what the borrower should do, imply financial risk, or suggest capital action.]

**Credit Opinion:**
NOT GENERATED — reserved for qualified credit officer review.

---
"""

def build_card_payload(asset):
    fp = asset["fingerprint"]
    z_context = (
        f"Z={asset['z_score']} (μ={asset['z_mu']}, σ={asset['z_sigma']}, "
        f"population: all {asset['boro']} properties this run — cross-sectional snapshot)"
        if asset["z_score"] is not None else "Z=N/A (insufficient borough sample)"
    )
    escalation_text = (
        "\n".join(f"  ⚠️  {f}" for f in asset["escalation_flags"])
        if asset["escalation_flags"] else "  No escalation rules triggered"
    )
    # FIX 2: Each driver on its own line in the payload — prevents LLM concatenation
    attribution_text = "\n".join(f"  {d}" for d in asset["causal_attribution"])

    event_rows = "\n".join(
        f"| {i+1} | {ev['event_type'].replace('_',' ').title()} | {ev['event_class']} "
        f"| {ev['event_status']} | {ev['readable_desc']} | +{ev['impact_score']} pts "
        f"| {ev['event_date']} | {ev['days_outstanding']} days |"
        for i, ev in enumerate(asset["events"])
    )
    return f"""
PROPERTY: {asset['address']} | BOROUGH: {asset['boro']}
SCORE: {asset['current']}/100 | MOMENTUM(30D): {asset['momentum_30d']} | PRIOR(31-60D): {asset['prior_momentum']} | VELOCITY: {asset['velocity']:+d} | TREND: {asset['trend']}
BUCKET: {asset['bucket_label']} — {asset['bucket_action']}
Z-SCORE: {z_context} | ANOMALY: {asset['anomaly'] or 'None'}
FINGERPRINT: Fire={fp['fire_events']} Structural={fp['structural_events']} Fresh={fp['fresh_event_count']} Recurrence={fp['recurrence_index']} Dominant={fp['dominant_type']}
ESCALATION FLAGS:
{escalation_text}
CAUSAL ATTRIBUTION (each driver is a separate line — do not concatenate):
{attribution_text}
EVENTS:
| # | Event Type | Event Class | Status | Description | Impact | Date | Days Out |
|---|-----------|-------------|--------|-------------|--------|------|----------|
{event_rows}
---
"""

# =====================================================================
# 10. BATCHED GROQ EXECUTION
# =====================================================================
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

print("\n" + "="*70)
print(f"📋 CRE SURVEILLANCE PLATFORM v8.7 | {run_datetime_str}")
print(f"📊 SYSTEMIC RISK INDEX  : {systemic_index}/100")
print(f"📐 RISK COMPONENT INDICATORS:\n{systemic_decomp}")
print(f"🏘️  TOP WEIGHTED CLUSTERS (watchlist-scoped, post-dedup):")
for ct, exp in top_clusters:
    print(f"   {ct.replace('_',' ').title()}: {round(exp,1)}")
print(f"📋 WATCHLIST COUNT      : {watchlist_count} properties")
print(f"📐 Z-SCORE BASIS        : Cross-sectional borough snapshot (full portfolio pass)")
print("="*70 + "\n")

# --- CALL 1: Matrix + Data Quality Gate + Syndication Summary ---
print("⏳ Generating Section 1: Matrix, Data Quality Gate, Syndication Summary...\n")
try:
    r1 = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt_1}],
        temperature=0.0,
        max_tokens=4000
    )
    print(r1.choices[0].message.content)
except Exception as e:
    print(f"❌ Section 1 Compiler Failure: {e}")

print("\n" + "="*70)

# --- CALLS 2+: Batched Property Cards ---
batches      = [active_watchlist[i:i+BATCH_SIZE] for i in range(0, len(active_watchlist), BATCH_SIZE)]
total_batches = len(batches)

print(f"\n## 📋 FULL PROPERTY CARDS ({watchlist_count} properties | {total_batches} batches of max {BATCH_SIZE})\n")

BATCH_SLEEP_SECONDS = 22   # Wait between batches to respect 6000 TPM window
MAX_RETRIES         = 3

for batch_num, batch in enumerate(batches, 1):
    addresses     = ", ".join(a["address"] for a in batch)
    print(f"⏳ Batch {batch_num}/{total_batches}: {addresses}...\n")
    batch_payload = "".join(build_card_payload(a) for a in batch)
    card_prompt   = CARD_PROMPT_TEMPLATE.format(batch_payload=batch_payload)

    success = False
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": card_prompt}],
                temperature=0.0,
                max_tokens=3000
            )
            print(r.choices[0].message.content)
            success = True
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str:
                wait = BATCH_SLEEP_SECONDS * attempt
                print(f"⚠️  Rate limit hit (attempt {attempt}/{MAX_RETRIES}). Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                print(f"❌ Batch {batch_num} Compiler Failure: {e}")
                break

    if not success:
        print(f"❌ Batch {batch_num} failed after {MAX_RETRIES} attempts. Skipping.")

    # Sleep between successful batches to avoid hitting TPM ceiling on next call
    if batch_num < total_batches:
        print(f"   ⏸️  Waiting {BATCH_SLEEP_SECONDS}s before next batch...\n")
        time.sleep(BATCH_SLEEP_SECONDS)

print("\n" + "="*70)

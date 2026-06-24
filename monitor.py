import os
import requests
from datetime import datetime, timedelta
from groq import Groq

print("🚀 Launching Version 8.3: Institutional Production Surveillance Engine...")

# =====================================================================
# 1. GOVERNANCE-APPROVED CALIBRATION METRICS
# =====================================================================
BOROUGH_BASELINES = {
    "MANHATTAN": 25,
    "BRONX": 35,
    "BROOKLYN": 30,
    "QUEENS": 25,
    "STATEN ISLAND": 20,
    "NYC": 25
}

RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35,
    "STRUCTURAL_INSTABILITY": 30,
    "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20,
    "LITIGATION_GENERAL": 15,
    "STANDARD_VIOLATION": 5,
    "REMEDIATION_EVENT": 0
}

FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90
WATCHLIST_SCORE_THRESHOLD = 65

cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str = cutoff_date.strftime("%Y-%m-%d")

properties_db = {}
exceptions_log = []
seen_events = set()


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
    num = str(
        item.get("house_number")
        or item.get("buildingnumber")
        or item.get("house_no")
        or ""
    ).strip()

    street = str(
        item.get("street_name")
        or item.get("streetname")
        or item.get("street")
        or ""
    ).strip()

    if not num or not street:
        return None

    return f"{num} {street}"


# =====================================================================
# 2. VALIDATED TIME-SERIES DATA INGESTION ENGINE
# =====================================================================

lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"


# ---------------------------------------------------------------------
# Housing Litigation Data
# ---------------------------------------------------------------------

try:
    res = requests.get(
        lit_url,
        params={
            "$where": f"caseopendate > '{cutoff_str}'",
            "$order": "caseopendate DESC",
            "$limit": 150
        },
        timeout=15
    )

    res.raise_for_status()
    lit_data = res.json()

    if isinstance(lit_data, list):

        for record in lit_data:

            rec_id = record.get("litigationid", "UNK")

            event_date = parse_nyc_date(
                record.get("caseopendate")
            )

            if not event_date:
                exceptions_log.append(
                    f"Litigation ID {rec_id}: Invalid timestamp."
                )
                continue

            addr = clean_address(record)

            if not addr:
                exceptions_log.append(
                    f"Litigation ID {rec_id}: Incomplete address."
                )
                continue

            boro_code = str(
                record.get("boroid", "NYC")
            )

            boro_name = {
                "1": "MANHATTAN",
                "2": "BRONX",
                "3": "BROOKLYN",
                "4": "QUEENS",
                "5": "STATEN ISLAND"
            }.get(boro_code, "NYC")

            full_key = f"{addr}, {boro_name}"

            if full_key not in properties_db:
                properties_db[full_key] = {
                    "boro": boro_name,
                    "events": []
                }

            case_type = str(
                record.get("casetype", "")
            ).upper()

            if any(
                k in case_type for k in [
                    "CODE COMPLIANCE",
                    "COMPLIED",
                    "CORRECTED",
                    "DISMISSED"
                ]
            ):
                event_cat = "REMEDIATION_EVENT"

            else:
                if "HARASSMENT" in case_type:
                    event_cat = "HARASSMENT_CLAIM"
                else:
                    event_cat = "LITIGATION_GENERAL"

            event_key = (
                f"{full_key}_{rec_id}_"
                f"{event_date}_{event_cat}"
            )

            if event_key in seen_events:
                continue

            seen_events.add(event_key)

            properties_db[full_key]["events"].append({
                "cat": event_cat,
                "age_days": (
                    datetime.now() - event_date
                ).days,
                "desc": f"Litigation: {case_type[:60]}"
            })

except Exception as e:

    exceptions_log.append(
        f"Critical Litigation API Failure: {e}"
    )


# ---------------------------------------------------------------------
# DOB Violation Data
# ---------------------------------------------------------------------

try:

    res = requests.get(
        viol_url,
        params={
            "$where": f"issue_date > '{cutoff_str}'",
            "$order": "issue_date DESC",
            "$limit": 150
        },
        timeout=15
    )

    res.raise_for_status()

    viol_data = res.json()

    if isinstance(viol_data, list):

        for record in viol_data:

            rec_id = record.get(
                "violation_number",
                "UNK"
            )

            event_date = parse_nyc_date(
                record.get("issue_date")
            )

            if not event_date:
                exceptions_log.append(
                    f"Violation ID {rec_id}: Invalid timestamp."
                )
                continue

            addr = clean_address(record)

            if not addr:
                exceptions_log.append(
                    f"Violation ID {rec_id}: Incomplete address."
                )
                continue

            boro_raw = str(
                record.get("boro", "")
                or record.get("borough", "")
                or ""
            ).strip().upper()

            BORO_LOOKUP = {
                "1": "MANHATTAN",
                "2": "BRONX",
                "3": "BROOKLYN",
                "4": "QUEENS",
                "5": "STATEN ISLAND",
                "MN": "MANHATTAN",
                "BX": "BRONX",
                "BK": "BROOKLYN",
                "QN": "QUEENS",
                "SI": "STATEN ISLAND"
            }

            boro_name = BORO_LOOKUP.get(
                boro_raw,
                "NYC"
            )

            full_key = f"{addr}, {boro_name}"

            if full_key not in properties_db:
                properties_db[full_key] = {
                    "boro": boro_name,
                    "events": []
                }

            desc = str(
                record.get(
                    "description",
                    ""
                )
            ).upper()

            severity = str(
                record.get(
                    "violation_category",
                    ""
                )
            ).upper()

            if any(
                k in desc for k in [
                    "CODE COMPLIANCE",
                    "COMPLIED",
                    "CORRECTED",
                    "DISMISSED"
                ]
            ):
                event_cat = "REMEDIATION_EVENT"

            elif "FIRE" in desc:
                event_cat = "FIRE_DAMAGE"

            elif (
                "FACADE" in desc
                or "COLLAPSE" in desc
            ):
                event_cat = "STRUCTURAL_INSTABILITY"

            elif (
                "CLASS 1" in severity
                or "HAZARDOUS" in severity
            ):
                event_cat = "HAZARDOUS_CLASS_1"

            else:
                event_cat = "STANDARD_VIOLATION"

            event_key = (
                f"{full_key}_{rec_id}_"
                f"{event_date}_{event_cat}"
            )

            if event_key in seen_events:
                continue

            seen_events.add(event_key)

            properties_db[full_key]["events"].append({
                "cat": event_cat,
                "age_days": (
                    datetime.now() - event_date
                ).days,
                "desc": f"DOB: {desc[:60]}"
            })

except Exception as e:

    exceptions_log.append(
        f"Critical Violation API Failure: {e}"
    )
# =====================================================================
# 3. ZERO DATA EXCLUSION GATE
# =====================================================================

if not properties_db or all(
    len(v["events"]) == 0 for v in properties_db.values()
):
    print("\n===================================================")
    print("📋 CRE SURVEILLANCE PLATFORM: VERSION 8.3")
    print("===================================================")
    print("⚠️ No qualifying public-record observations detected.")
    print("===================================================")
    exit()


# =====================================================================
# 4. DETERMINISTIC SCORE ENGINE
# =====================================================================

calculated_portfolio = []

for addr, asset in properties_db.items():

    boro = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)

    current_score = baseline
    prior_risk_baseline = baseline

    category_counts = {}
    event_traces = []

    sorted_events = sorted(
        asset["events"],
        key=lambda x: x["age_days"],
        reverse=True
    )

    for ev in sorted_events:

        cat = ev["cat"]

        category_counts[cat] = (
            category_counts.get(cat, 0) + 1
        )

        is_recurring = category_counts[cat] > 1

        is_fresh = (
            ev["age_days"] <= FRESH_WINDOW_DAYS
        )

        if is_fresh:
            lifecycle = (
                "NEW EVENT"
                if not is_recurring
                else "PERSISTENT CONDITION"
            )
        else:
            lifecycle = (
                "BACKGROUND CONDITION"
                if not is_recurring
                else "PERSISTENT CONDITION"
            )

        amplifier = 1.35 if is_recurring else 1.00

        base_points = RULE_SCORE_MATRIX.get(cat, 5)

        if is_fresh:
            points_added = base_points * amplifier
            current_score += points_added

        else:
            points_added = (
                base_points * amplifier * 0.70
            )

            current_score += points_added
            prior_risk_baseline += points_added

        event_traces.append(
            f"Type: {ev['desc']} | "
            f"Classification: {lifecycle} | "
            f"Score Impact: +{int(points_added)}"
        )

    current_score = min(int(current_score), 100)
    prior_risk_baseline = min(
        int(prior_risk_baseline),
        100
    )

    net_risk_acceleration = (
        current_score - prior_risk_baseline
    )

    calculated_portfolio.append({
        "address": addr,
        "boro": boro,
        "current": current_score,
        "prior": prior_risk_baseline,
        "velocity": net_risk_acceleration,
        "traces": event_traces
    })


# =====================================================================
# 5. WATCHLIST CONSTRUCTION
# =====================================================================

active_watchlist = [
    asset for asset in calculated_portfolio
    if asset["current"] >= WATCHLIST_SCORE_THRESHOLD
]

active_watchlist = sorted(
    active_watchlist,
    key=lambda x: (
        x["current"],
        x["velocity"]
    ),
    reverse=True
)

# Governance display cap
active_watchlist = active_watchlist[:20]


# =====================================================================
# 6. REPORT DATA PAYLOAD
# =====================================================================

watchlist_count = len(active_watchlist)

data_context_payload = (
    f"WATCHLIST ASSETS ({watchlist_count} TOTAL):\n\n"
)

for asset in active_watchlist:

    data_context_payload += (
        f"ADDRESS: {asset['address']}\n"
        f"Current Score: {asset['current']}/100\n"
        f"Prior Risk Baseline: {asset['prior']}/100\n"
        f"Net Risk Acceleration: {asset['velocity']}\n"
        f"Observed Public-Record Events:\n"
        + "\n".join(
            f"- {trace}"
            for trace in asset["traces"]
        )
        + "\n\n"
    )

exceptions_payload = (
    "DATA QUALITY EXCLUSIONS:\n"
)

if exceptions_log:
    for exc in exceptions_log[:5]:
        exceptions_payload += (
            f"- {exc}\n"
        )
else:
    exceptions_payload += (
        "- None\n"
    )


# =====================================================================
# 7. INSTITUTIONAL REPORTING COMPILER
# =====================================================================

prompt = f"""
You are an institutional commercial real estate
collateral surveillance reporting compiler.

The following information comes from a deterministic,
rule-based monitoring engine.

REPORTING GOVERNANCE RULES:

1. Higher scores indicate greater observed collateral
risk based solely on public-record severity,
persistence, and timing.

2. Use ONLY the exact assets provided.
Do NOT rank, remove, truncate, or create a Top 5 list.
Display all {watchlist_count} assets exactly as supplied.

3. Event descriptions must be reproduced exactly.
Do not interpret, expand, or infer additional conditions.

4. Use the classifications exactly as provided:
NEW EVENT
PERSISTENT CONDITION
BACKGROUND CONDITION

5. Do not infer:
- borrower financial distress
- liquidity weakness
- DSCR deterioration
- default probability
- valuation impact
- required capital expenditure

6. Preserve all numerical values exactly:
Current Score,
Prior Risk Baseline,
Net Risk Acceleration,
Score Impact.

7. Format the report exactly with these sections:

INSTITUTIONAL COLLATERAL SURVEILLANCE REPORT

WATCHLIST COUNT
RISK THRESHOLD
COLLATERAL SURVEILLANCE WATCHLIST
DATA QUALITY EXCLUSIONS

8. End with exactly one sentence describing
the appropriate collateral monitoring cadence only.

INPUT DATA:

{data_context_payload}

{exceptions_payload}
"""


# =====================================================================
# 8. GROQ EXECUTION LAYER
# =====================================================================

client = Groq(
    api_key=os.environ.get(
        "GROQ_API_KEY"
    )
)

try:

    response = (
        client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.0,
            max_tokens=4000
        )
    )

    print("\n===================================================")
    print(
        response.choices[0].message.content
    )
    print("===================================================")

except Exception as e:

    print(
        f"❌ Layer 5 Institutional Reporting Failure: {e}"
    )

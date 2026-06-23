import os
import requests
from datetime import datetime, timedelta
from groq import Groq

# =====================================================================
# SYSTEM INITIALIZATION & HARDCODED CONFIGURATION (LAYER 2: RULES)
# =====================================================================
print("🧮 Launching Version 7.2: Decoupled Rules Engine & Narrative System...")

BOROUGH_BASELINES = {
    "MANHATTAN": 25, 
    "BRONX": 35, 
    "BROOKLYN": 30, 
    "QUEENS": 25, 
    "STATEN ISLAND": 20, 
    "NYC": 25
}

# Deterministic, unchangeable rules engine weight matrix
RULE_SCORE_MATRIX = {
    "FIRE_DAMAGE": 35,
    "STRUCTURAL_INSTABILITY": 30,
    "HARASSMENT_CLAIM": 25,
    "HAZARDOUS_CLASS_1": 20,
    "LITIGATION_GENERAL": 15,
    "STANDARD_VIOLATION": 5
}

# Strict Time Constraints
FRESH_WINDOW_DAYS = 30
TOTAL_INGESTION_DAYS = 90

# Calculate exact ISO format string for Socrata queries
cutoff_date = datetime.now() - timedelta(days=TOTAL_INGESTION_DAYS)
cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%S')

properties_db = {}
exceptions_log = []

def parse_nyc_date(date_str):
    """
    Normalizes multiple NYC OpenData timestamp variations 
    to guarantee a valid datetime object for age calculations.
    """
    if not date_str:
        return datetime.now()
    
    # Clean the date input by splitting at the 'T' separator and stripping characters
    clean_date = str(date_str).split("T")[0].replace("-", "").strip()
    
    try:
        # Catch standard flat string formats (e.g., '20260326')
        return datetime.strptime(clean_date, "%Y%m%d")
    except ValueError:
        try:
            # Fallback for standard dash notation (e.g., '2026-03-26')
            return datetime.strptime(clean_date, "%Y-%m-%d")
        except Exception:
            # Fallback to prevent application crash if column schema breaks
            return datetime.now()

def clean_address(item):
    """Enforces absolute field quality filters across address keys."""
    num = str(item.get("house_number") or item.get("buildingnumber") or item.get("house_no") or "").strip()
    street = str(item.get("street_name") or item.get("streetname") or item.get("street") or "").strip()
    if not num or not street: 
        return None
    return f"{num} {street}"

# =====================================================================
# LAYER 1: DATA INGESTION (WITH SEVER-SIDE SORT & DATE ADAPTER)
# =====================================================================
lit_url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
viol_url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"

# Ingest Housing Litigations
try:
    lit_params = {
        "$where": f"caseopendate > '{cutoff_str}'", 
        "$order": "caseopendate DESC",
        "$limit": 300
    }
    res = requests.get(lit_url, params=lit_params, timeout=15)
    lit_data = res.json()
    
    if isinstance(lit_data, list):
        for record in lit_data:
            addr = clean_address(record)
            if not addr:
                exceptions_log.append(f"Litigation ID {record.get('litigationid', 'UNK')}: Missing Address Metadata")
                continue
            
            boro_code = str(record.get("boroid", "NYC"))
            boro_name = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN", "4": "QUEENS", "5": "STATEN ISLAND"}.get(boro_code, "NYC")
            full_key = f"{addr}, {boro_name}"
            
            if full_key not in properties_db:
                properties_db[full_key] = {"boro": boro_name, "events": []}
                
            case_type = str(record.get("casetype", "")).upper()
            event_cat = "HARASSMENT_CLAIM" if "HARASSMENT" in case_type else "LITIGATION_GENERAL"
            open_date = parse_nyc_date(record.get("caseopendate"))
            
            properties_db[full_key]["events"].append({
                "cat": event_cat, 
                "age_days": (datetime.now() - open_date).days, 
                "desc": f"Litigation: {case_type}"
            })
except Exception as e: 
    print(f"⚠️ Layer 1 Litigation Ingestion Bypass: {e}")

# Ingest DOB Structural Violations
try:
    viol_params = {
        "$where": f"issue_date > '{cutoff_str}'", 
        "$order": "issue_date DESC",
        "$limit": 300
    }
    res = requests.get(viol_url, params=viol_params, timeout=15)
    viol_data = res.json()
    
    if isinstance(viol_data, list):
        for record in viol_data:
            addr = clean_address(record)
            if not addr:
                exceptions_log.append(f"Violation ID {record.get('violation_number', 'UNK')}: Missing Address Metadata")
                continue
            
            boro_raw = str(record.get("boro", "NYC")).upper()
            if "MANH" in boro_raw: boro_name = "MANHATTAN"
            elif "BRONX" in boro_raw: boro_name = "BRONX"
            elif "BROOK" in boro_raw: boro_name = "BROOKLYN"
            elif "QUEENS" in boro_raw: boro_name = "QUEENS"
            elif "STATEN" in boro_raw: boro_name = "STATEN ISLAND"
            else: boro_name = "NYC"
            
            full_key = f"{addr}, {boro_name}"
            
            if full_key not in properties_db:
                properties_db[full_key] = {"boro": boro_name, "events": []}
                
            desc = str(record.get("description", "")).upper()
            severity = str(record.get("violation_category", "")).upper()
            open_date = parse_nyc_date(record.get("issue_date"))
            
            if "FIRE" in desc: event_cat = "FIRE_DAMAGE"
            elif "FACADE" in desc or "COLLAPSE" in desc: event_cat = "STRUCTURAL_INSTABILITY"
            elif "CLASS 1" in severity or "HAZARDOUS" in severity: event_cat = "HAZARDOUS_CLASS_1"
            else: event_cat = "STANDARD_VIOLATION"
            
            properties_db[full_key]["events"].append({
                "cat": event_cat, 
                "age_days": (datetime.now() - open_date).days, 
                "desc": f"DOB: {desc[:25]}"
            })
except Exception as e: 
    print(f"⚠️ Layer 1 Violation Ingestion Bypass: {e}")

# =====================================================================
# LAYER 2: DETERMINISTIC SCORE ENGINE (PURE MATHEMATICS)
# =====================================================================
calculated_portfolio = []

for addr, asset in properties_db.items():
    boro = asset["boro"]
    baseline = BOROUGH_BASELINES.get(boro, 25)
    
    current_score = baseline
    historic_score = baseline
    
    cat_counts = {}
    event_traces = []
    
    # Sort events chronologically to guarantee deterministic mathematical stacking
    sorted_events = sorted(asset["events"], key=lambda x: x["age_days"], reverse=True)
    
    for ev in sorted_events:
        cat = ev["cat"]
        cat_counts[cat] = cat_counts.

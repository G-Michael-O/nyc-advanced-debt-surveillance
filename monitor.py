def parse_nyc_date(date_str):
    """
    Normalizes multiple NYC OpenData timestamp variations 
    to guarantee a valid datetime object.
    """
    if not date_str:
        return datetime.now()
    
    # Strip any trailing time or timezone metadata
    clean_date = str(date_str).split("T")[0].replace("-", "").strip()
    
    try:
        # Fallback for flat string formats (e.g., '20260326')
        return datetime.strptime(clean_date, "%Y%m%d")
    except ValueError:
        try:
            # Fallback for standard dash notation (e.g., '20260326') if string slicing shifts
            return datetime.strptime(clean_date, "%Y-%m-%d")
        except Exception:
            # Safe operational default if column formatting collapses entirely
            return datetime.now()

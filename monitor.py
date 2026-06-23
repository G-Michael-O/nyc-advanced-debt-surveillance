import requests
from datetime import datetime, timedelta

DAYS_BACK = 60
cutoff = (datetime.now() - timedelta(days=DAYS_BACK)).strftime('%Y-%m-%dT%H:%M:%S')

def fetch_litigation():
    url = "https://data.cityofnewyork.us/resource/59kj-x8nc.json"
    return requests.get(url, params={
        "$where": f"caseopendate > '{cutoff}'",
        "$limit": 500
    }).json()

def fetch_violations():
    url = "https://data.cityofnewyork.us/resource/3h2n-5cm9.json"
    return requests.get(url, params={
        "$where": f"issue_date > '{cutoff}'",
        "$limit": 500
    }).json()

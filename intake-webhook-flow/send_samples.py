"""Post a few sample requests to the running flow, like a webhook would."""
import json
import os
import urllib.request

URL = os.environ.get("FLOW_URL", "http://127.0.0.1:8787/intake")
SAMPLES = [
    {"name": "Avery", "team": "Enterprise Sales", "message": "Need a license for the meeting notes tool before a customer call today."},
    {"name": "Jordan", "team": "Customer Success", "message": "The renewal dashboard shows an error and is not working since this morning."},
    {"name": "Sam", "team": "Partners", "message": "Idea: could we automate the weekly partner digest from the CRM?"},
    {"name": "Riley", "team": "Marketing", "message": "Where do I find the approved prompt library?"},
]
for s in SAMPLES:
    req = urllib.request.Request(URL, data=json.dumps(s).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        row = json.load(r)
    print(f"{row['request_id']}  {row['category']:<8} {row['urgency']:<6} {row['status']:<12} {row['summary']}")

import requests
import json

api_key = "sk_live_u4HVi6BcO8Tb16_gxRkfTS-NcGem2LTOGF-28VMdt4E"
agent_id = "agt_RoX6Z1NhZK4eIC8J"

calls_resp = requests.get(
    "https://agentphone-production.up.railway.app/v1/calls?limit=10",
    headers={"Authorization": f"Bearer {api_key}"}
)
print("=== RECENT CALLS ===")
for c in calls_resp.json()[:5]:
    print(f"ID: {c['id']}")
    print(f"  from: {c.get('from_number')} → to: {c.get('to_number')}")
    print(f"  duration: {c.get('duration_seconds', '?')}s")
    print(f"  transcript: {json.dumps(c.get('transcript', []), indent=4)}")
    print()

events_resp = requests.get(
    "https://agentphone-production.up.railway.app/v1/events",
    params={"agent_id": agent_id},
    headers={"Authorization": f"Bearer {api_key}"}
)
print("=== PENDING EVENTS ===")
print(f"Pending: {events_resp.json().get('pending')}")
for e in events_resp.json().get("events", []):
    print(json.dumps(e, indent=2))
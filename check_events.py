import requests
import json

api_key = "sk_live_u4HVi6BcO8Tb16_gxRkfTS-NcGem2LTOGF-28VMdt4E"
agent_id = "agt_RoX6Z1NhZK4eIC8J"

# Get recent calls
calls_resp = requests.get(
    "https://agentphone-production.up.railway.app/v1/calls?limit=10",
    headers={"Authorization": f"Bearer {api_key}"}
)
calls = calls_resp.json()
if isinstance(calls, list):
    for c in calls[:3]:
        print(f"Call {c['id']}:")
        print(f"  Duration: {c['duration_seconds']}s")
        print(f"  Transcript: {c.get('transcript', '[]')}")
        print()
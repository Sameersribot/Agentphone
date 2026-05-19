"""
Reconfigure all active SignalWire numbers to use the correct
StatusCallback URL for inbound call billing.

Run this AFTER deploying the billing fix. Can be run locally if
SignalWire env vars are in .env, or on Railway.
"""
import asyncio
import asyncpg
import httpx
import sys
sys.path.insert(0, ".")

from agentline.config import settings

if not all([settings.SIGNALWIRE_PROJECT_ID, settings.SIGNALWIRE_TOKEN, settings.SIGNALWIRE_SPACE_URL]):
    print("ERROR: Missing SignalWire credentials.")
    print("Add SIGNALWIRE_PROJECT_ID, SIGNALWIRE_TOKEN, and SIGNALWIRE_SPACE_URL to .env")
    exit(1)

SW_BASE = f"https://{settings.SIGNALWIRE_SPACE_URL}/api/laml/2010-04-01/Accounts/{settings.SIGNALWIRE_PROJECT_ID}"
AUTH = (settings.SIGNALWIRE_PROJECT_ID, settings.SIGNALWIRE_TOKEN)
BASE_URL = settings.base_url_clean


async def main():
    db = await asyncpg.connect(settings.db_dsn)
    rows = await db.fetch(
        "SELECT id, phone_number, provider_id, account_id FROM phone_numbers WHERE status = 'active'"
    )
    await db.close()

    print(f"Reconfiguring {len(rows)} active numbers...")
    print(f"StatusCallback -> {BASE_URL}/signalwire/inbound_hangup")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for r in rows:
            provider_id = r["provider_id"]
            phone = r["phone_number"]
            url = f"{SW_BASE}/IncomingPhoneNumbers/{provider_id}.json"
            data = {
                "VoiceUrl": f"{BASE_URL}/signalwire/inbound",
                "VoiceMethod": "POST",
                "SmsUrl": f"{BASE_URL}/signalwire/sms",
                "SmsMethod": "POST",
                "StatusCallback": f"{BASE_URL}/signalwire/inbound_hangup",
                "StatusCallbackMethod": "POST",
            }
            try:
                resp = await client.post(url, auth=AUTH, data=data)
                resp.raise_for_status()
                print(f"  OK: {phone} ({provider_id})")
            except Exception as e:
                print(f"  FAIL: {phone} ({provider_id}) - {e}")

    print("\nDone! All numbers now have correct StatusCallback for billing.")

asyncio.run(main())

"""List all phone numbers and reconfigure their webhooks."""
import asyncio
import asyncpg
import httpx

DATABASE_URL = "postgresql://postgres.chxozazdbgnyedmkmwif:Sameer%40Agentline26@aws-1-us-west-1.pooler.supabase.com:5432/postgres"

async def main():
    db = await asyncpg.connect(DATABASE_URL)
    rows = await db.fetch(
        "SELECT id, phone_number, provider_id, account_id, status FROM phone_numbers WHERE status = 'active' ORDER BY created_at DESC"
    )
    print(f"=== ACTIVE PHONE NUMBERS ({len(rows)}) ===")
    for r in rows:
        print(f"  {r['id']} | {r['phone_number']} | provider={r['provider_id']} | acct={r['account_id']}")
    await db.close()

asyncio.run(main())

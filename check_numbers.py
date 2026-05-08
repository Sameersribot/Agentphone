"""Quick script to check what numbers exist in the database and their provider routing."""
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return

    conn = await asyncpg.connect(db_url)

    print("=" * 70)
    print("PHONE NUMBERS IN DATABASE")
    print("=" * 70)
    rows = await conn.fetch(
        "SELECT id, agent_id, provider_id, phone_number, country, status FROM phone_numbers ORDER BY created_at DESC"
    )
    if not rows:
        print("  (none)")
    for r in rows:
        provider = "SignalWire" if r["country"] == "US" else "Plivo"
        print(f"  {r['id']} | agent={r['agent_id']} | {r['phone_number']} | country={r['country']} | status={r['status']} | routes_via={provider}")

    print()
    print("=" * 70)
    print("AGENTS")
    print("=" * 70)
    agents = await conn.fetch("SELECT id, name, account_id FROM agents ORDER BY created_at DESC")
    if not agents:
        print("  (none)")
    for a in agents:
        num = await conn.fetchrow(
            "SELECT phone_number, country, status FROM phone_numbers WHERE agent_id=$1 AND status='active'",
            a["id"]
        )
        if num:
            print(f"  {a['id']} | {a['name']} | number={num['phone_number']} ({num['country']}) | status={num['status']}")
        else:
            print(f"  {a['id']} | {a['name']} | NO ACTIVE NUMBER")

    print()
    print("=" * 70)
    print("RECENT CALLS (last 5)")
    print("=" * 70)
    calls = await conn.fetch(
        "SELECT id, agent_id, from_number, to_number, status, started_at FROM calls ORDER BY started_at DESC LIMIT 5"
    )
    if not calls:
        print("  (none)")
    for c in calls:
        print(f"  {c['id']} | agent={c['agent_id']} | {c['from_number']} -> {c['to_number']} | status={c['status']} | {c['started_at']}")

    await conn.close()

asyncio.run(main())

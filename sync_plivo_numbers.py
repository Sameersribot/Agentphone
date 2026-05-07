"""
Apply pending migrations and sync Plivo numbers into the database.
"""
import asyncio
import os
import secrets
from dotenv import load_dotenv
load_dotenv()
import asyncpg


NUMBERS = ["+918031338878", "+918031338876", "+918035374340"]
ACCOUNT_ID = "acct_WMH4W8KXaYiHY6Cz"
AGENT_ID = "agt_RoX6Z1NhZK4eIC8J"


async def main():
    db_url = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://")
    conn = await asyncpg.connect(db_url)

    # Step 1: Apply migration 001 — rename telnyx columns to provider columns
    print("=== APPLYING MIGRATIONS ===")
    try:
        # Check if telnyx_id still exists
        col_check = await conn.fetchval("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='phone_numbers' AND column_name='telnyx_id'
        """)
        if col_check:
            print("  Renaming telnyx_id -> provider_id ...")
            await conn.execute("ALTER TABLE phone_numbers RENAME COLUMN telnyx_id TO provider_id")
            print("  OK")
        else:
            print("  phone_numbers.provider_id already exists, skipping")

        col_check2 = await conn.fetchval("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='calls' AND column_name='telnyx_call_id'
        """)
        if col_check2:
            print("  Renaming telnyx_call_id -> provider_call_id ...")
            await conn.execute("ALTER TABLE calls RENAME COLUMN telnyx_call_id TO provider_call_id")
            print("  OK")
        else:
            print("  calls.provider_call_id already exists, skipping")

        col_check3 = await conn.fetchval("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name='messages' AND column_name='telnyx_message_id'
        """)
        if col_check3:
            print("  Renaming telnyx_message_id -> provider_message_id ...")
            await conn.execute("ALTER TABLE messages RENAME COLUMN telnyx_message_id TO provider_message_id")
            print("  OK")
        else:
            print("  messages.provider_message_id already exists, skipping")
    except Exception as e:
        print(f"  Migration error: {e}")

    # Step 2: Apply migration 002 — one active number per agent index
    print("\n  Creating one-active-number-per-agent index...")
    try:
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_number_per_agent
            ON phone_numbers (agent_id)
            WHERE status = 'active'
        """)
        print("  OK")
    except Exception as e:
        print(f"  Index error (may already exist): {e}")

    # Step 3: Verify schema
    print("\n=== CURRENT SCHEMA ===")
    cols = await conn.fetch("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'phone_numbers' ORDER BY ordinal_position
    """)
    print("  Columns:", [c['column_name'] for c in cols])

    # Step 4: Insert numbers — only FIRST one as active (one-per-agent rule)
    print(f"\n=== INSERTING {len(NUMBERS)} NUMBERS ===")

    # Check what's already in the DB
    existing = await conn.fetch("SELECT phone_number FROM phone_numbers WHERE account_id=$1", ACCOUNT_ID)
    existing_set = {r['phone_number'] for r in existing}

    inserted = 0
    for i, phone in enumerate(NUMBERS):
        if phone in existing_set:
            print(f"  SKIP {phone} (already in DB)")
            continue

        number_id = f"num_{secrets.token_urlsafe(12)}"
        provider_id = phone.lstrip("+")
        country = "IN"

        # Only first one gets active + agent assignment (one-per-agent constraint)
        if i == 0:
            status = "active"
            agent_id = AGENT_ID
        else:
            status = "inactive"
            agent_id = None  # Unassigned — can be attached later

        try:
            await conn.execute(
                """INSERT INTO phone_numbers
                   (id, account_id, agent_id, provider_id, phone_number, country, status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                number_id, ACCOUNT_ID, agent_id, provider_id, phone, country, status,
            )
            agent_label = agent_id or "(unassigned)"
            print(f"  OK  {phone} -> {number_id} | agent={agent_label} | status={status}")
            inserted += 1
        except Exception as e:
            print(f"  FAIL {phone}: {e}")

    # Step 5: Final verification
    print(f"\n=== FINAL STATE ===")
    rows = await conn.fetch(
        "SELECT id, phone_number, agent_id, status FROM phone_numbers WHERE account_id=$1 ORDER BY created_at",
        ACCOUNT_ID,
    )
    for r in rows:
        print(f"  {r['id']}  {r['phone_number']}  agent={r['agent_id']}  status={r['status']}")

    print(f"\nInserted {inserted} numbers. Total in DB: {len(rows)}")
    await conn.close()


asyncio.run(main())

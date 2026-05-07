"""Apply migration 003: create call_responses table."""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def main():
    db_url = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://")
    conn = await asyncpg.connect(db_url)

    print("Creating call_responses table...")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS call_responses (
            id SERIAL PRIMARY KEY,
            call_id TEXT REFERENCES calls(id) ON DELETE CASCADE,
            response_text TEXT NOT NULL,
            spoken BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    print("OK")

    print("Creating index...")
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_call_responses_pending
        ON call_responses (call_id, spoken)
        WHERE spoken = false
    """)
    print("OK")

    # Verify
    cols = await conn.fetch("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'call_responses' ORDER BY ordinal_position
    """)
    print(f"call_responses columns: {[c['column_name'] for c in cols]}")

    await conn.close()
    print("Done!")

asyncio.run(main())

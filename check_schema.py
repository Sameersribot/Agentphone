"""Quick script to check actual DB schema and insert numbers."""
import asyncio
import os
import secrets
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def main():
    db_url = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://")
    conn = await asyncpg.connect(db_url)

    # Check actual columns in phone_numbers table
    cols = await conn.fetch("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = 'phone_numbers'
        ORDER BY ordinal_position
    """)
    print("=== phone_numbers COLUMNS ===")
    for c in cols:
        print(f"  {c['column_name']:20s} {c['data_type']:20s} nullable={c['is_nullable']}  default={c['column_default']}")

    # Check existing numbers
    existing = await conn.fetch("SELECT * FROM phone_numbers")
    print(f"\n=== EXISTING ROWS ({len(existing)}) ===")
    for r in existing:
        print(f"  {dict(r)}")

    await conn.close()

asyncio.run(main())

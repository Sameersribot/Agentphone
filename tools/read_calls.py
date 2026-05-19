import asyncio
import os
import json
from dotenv import load_dotenv
import asyncpg

load_dotenv()

async def main():
    db_url = os.getenv("DATABASE_URL")
    print("Connecting to DB:", db_url.split("@")[-1] if db_url else "None")
    conn = await asyncpg.connect(db_url)
    
    rows = await conn.fetch("SELECT * FROM calls ORDER BY started_at DESC LIMIT 5")
    print("\n=== RECENT PRODUCTION CALLS ===")
    for r in rows:
        print(f"Call ID: {r['id']}")
        print(f"  Agent ID: {r['agent_id']}")
        print(f"  From: {r['from_number']} -> To: {r['to_number']}")
        print(f"  Direction: {r['direction']}, Status: {r['status']}")
        print(f"  Started: {r['started_at']}, Ended: {r['ended_at']}")
        print(f"  Duration: {r['duration_seconds']}s")
        print(f"  Transcript ({type(r['transcript'])}): {r['transcript']}")
        print("-" * 50)
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect(
        "postgresql://postgres.chxozazdbgnyedmkmwif:Sameer%40Agentline26@aws-1-us-west-1.pooler.supabase.com:5432/postgres"
    )
    # Read and execute schema.sql
    with open("schema.sql", "r") as f:
        sql = f.read()
    await conn.execute(sql)
    
    # Verify tables
    tables = await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name"
    )
    print("Created tables:")
    for t in tables:
        print(f"  - {t['table_name']}")
    await conn.close()

asyncio.run(main())

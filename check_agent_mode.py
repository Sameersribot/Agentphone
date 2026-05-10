import asyncio
import asyncpg

async def test():
    conn = await asyncpg.connect(
        host='aws-1-us-west-1.pooler.supabase.com',
        port=5432,
        user='postgres.chxozazdbgnyedmkmwif',
        password='Sameer@Agentline26',
        database='postgres'
    )
    row = await conn.fetchrow("SELECT * FROM agents WHERE id = 'agt_RoX6Z1NhZK4eIC8J'")
    print('Columns:', list(row.keys()))
    print('Data:', dict(row))
    await conn.close()

asyncio.run(test())
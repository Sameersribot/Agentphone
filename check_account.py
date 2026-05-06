import asyncio
import asyncpg

async def check():
    conn = await asyncpg.connect(
        "postgresql://postgres.chxozazdbgnyedmkmwif:Sameer%40Agentline26@aws-1-us-west-1.pooler.supabase.com:5432/postgres"
    )
    rows = await conn.fetch(
        "SELECT id, human_email, supabase_user_id FROM accounts WHERE human_email = $1",
        "ovalpodai@gmail.com",
    )
    if rows:
        print("Account found:")
        for r in rows:
            print(f"  id={r['id']}, email={r['human_email']}, supabase_uid={r['supabase_user_id']}")
        # Also get agent and api key info
        for r in rows:
            agents = await conn.fetch("SELECT id, name FROM agents WHERE account_id = $1", r['id'])
            keys = await conn.fetch("SELECT id, key_prefix FROM api_keys WHERE account_id = $1 AND revoked_at IS NULL", r['id'])
            print(f"  Agents: {[dict(a) for a in agents]}")
            print(f"  API Keys: {[dict(k) for k in keys]}")
    else:
        print("No account found for ovalpodai@gmail.com")
    await conn.close()

asyncio.run(check())

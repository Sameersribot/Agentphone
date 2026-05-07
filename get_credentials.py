import asyncio
import secrets
import bcrypt
from agentline.database import init_db, get_db_conn, close_db

async def run():
    await init_db()
    try:
        async with get_db_conn() as db:
            r = await db.fetchrow("SELECT id FROM accounts WHERE human_email='ovalpodai@gmail.com'")
            if not r:
                print("No account found")
                return
            act = r['id']
            
            ag = await db.fetchrow("SELECT id FROM agents WHERE account_id=$1", act)
            ph = await db.fetchrow("SELECT phone_number FROM phone_numbers WHERE account_id=$1", act)
            
            raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
            # Hash directly with bcrypt
            salt = bcrypt.gensalt()
            key_hash = bcrypt.hashpw(raw_key.encode('utf-8'), salt).decode('utf-8')
            key_id = f"key_{secrets.token_urlsafe(12)}"
            
            await db.execute(
                "INSERT INTO api_keys (id, account_id, key_hash, key_prefix) VALUES ($1,$2,$3,$4)",
                key_id, act, key_hash, raw_key[:12]
            )
            
            print("="*50)
            print("Account:", act)
            print("Agent:", ag['id'] if ag else 'None')
            print("Number:", ph['phone_number'] if ph else 'None')
            print("API Key:", raw_key)
            print("="*50)
    finally:
        await close_db()

if __name__ == '__main__':
    asyncio.run(run())

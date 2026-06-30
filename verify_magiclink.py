import asyncio
import secrets
from passlib.hash import bcrypt
import httpx
import logging

from agentline.config import settings
from agentline.database import init_db, get_db_conn, close_db
from agentline.plivo_client import provision_number

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def verify_and_provision(email: str, token_hash: str):
    await init_db()
    try:
        # 1. Verify magiclink via Supabase Auth
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.SUPABASE_URL}/auth/v1/verify",
                headers={
                    "apikey": settings.SUPABASE_ANON_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "type": "magiclink",
                    "token": token_hash,
                    "email": email,
                },
            )
            if response.status_code >= 400:
                logger.error(f"Supabase verification failed: {response.text}")
                return
            
            data = response.json()
            user = data.get("user")
            if not user:
                logger.error("No user in response.")
                return
            supabase_user_id = user["id"]
            logger.info(f"Verified Supabase user: {supabase_user_id}")

        async with get_db_conn() as conn:
            # 2. Check if account already exists
            existing = await conn.fetchrow("SELECT id FROM accounts WHERE human_email = $1", email)
            if existing:
                logger.warning("Account already exists.")
                return
            
            # 3. Create account
            account_id = f"acct_{secrets.token_urlsafe(12)}"
            await conn.execute(
                "INSERT INTO accounts (id, human_email, supabase_user_id) VALUES ($1, $2, $3)",
                account_id, email, supabase_user_id
            )
            
            # 4. Create agent
            agent_id = f"agt_{secrets.token_urlsafe(12)}"
            await conn.execute(
                "INSERT INTO agents (id, account_id, name) VALUES ($1, $2, $3)",
                agent_id, account_id, "My Agent"
            )
            
            # 5. Provision Plivo number
            try:
                number_data = await provision_number(country="US", agent_id=agent_id)
                number_id = f"num_{secrets.token_urlsafe(12)}"
                phone_number = number_data["phone_number"]
                await conn.execute(
                    "INSERT INTO phone_numbers (id, account_id, agent_id, provider_id, phone_number) VALUES ($1, $2, $3, $4, $5)",
                    number_id, account_id, agent_id, number_data["provider_id"], phone_number
                )
                logger.info(f"Provisioned number: {phone_number}")
            except Exception as e:
                logger.error(f"Failed to provision number: {e}")
                phone_number = None
                number_id = None
            
            # 6. Generate API key
            raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
            key_hash = bcrypt.hash(raw_key)
            key_id = f"key_{secrets.token_urlsafe(12)}"
            await conn.execute(
                "INSERT INTO api_keys (id, account_id, key_hash, key_prefix) VALUES ($1, $2, $3, $4)",
                key_id, account_id, key_hash, raw_key[:12]
            )
            
            print("\n" + "="*50)
            print("SUCCESS! ACCOUNT PROVISIONED IN PRODUCTION")
            print("="*50)
            print(f"Account ID:   {account_id}")
            print(f"Agent ID:     {agent_id}")
            if phone_number:
                print(f"Phone Number: {phone_number}")
            print(f"\nAPI KEY (Save this now!):")
            print(f"{raw_key}")
            print("="*50 + "\n")
            
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(verify_and_provision(
        email="ovalpodai@gmail.com",
        token_hash="1ad7406c6f1489215b934867a643d188e9b3381161202bc91a0c9198"
    ))

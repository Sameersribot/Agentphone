"""
Fix script: Create accounts for users who signed up through the UI
but don't have accounts table rows.

Affected users:
  - mark@respondin.org
  - mfarqu@salustechservices.com

This script:
1. Looks up their Supabase auth user IDs
2. Creates accounts table rows with $10 starting balance
3. Links the supabase_user_id so dashboard resolves correctly
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Use supabase-py for admin operations
from supabase import create_client

SUPABASE_URL = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

EMAILS = [
    "mark@respondin.org",
    "mfarqu@salustechservices.com",
]


def fix_users():
    for email in EMAILS:
        print(f"\n{'='*60}")
        print(f"Processing: {email}")

        # 1. Check if account already exists
        result = supabase.table("accounts").select("id, balance, supabase_user_id").eq("human_email", email).execute()
        if result.data:
            acct = result.data[0]
            print(f"  ✅ Account already exists: {acct['id']}, balance: ${acct['balance']}")
            continue

        # 2. Look up Supabase auth user
        try:
            # List users and find by email
            users_response = supabase.auth.admin.list_users()
            auth_user = None
            for u in users_response:
                # users_response can be a list of User objects
                if hasattr(u, '__iter__'):
                    for user in u:
                        if hasattr(user, 'email') and user.email == email:
                            auth_user = user
                            break
                elif hasattr(u, 'email') and u.email == email:
                    auth_user = u
                    break
            
            if auth_user:
                supabase_user_id = auth_user.id
                print(f"  Found Supabase auth user: {supabase_user_id}")
            else:
                supabase_user_id = None
                print(f"  ⚠️  No Supabase auth user found for {email}")
        except Exception as e:
            print(f"  ⚠️  Could not look up auth user: {e}")
            supabase_user_id = None

        # 3. Create account
        import secrets
        account_id = f"acct_{secrets.token_urlsafe(12)}"
        
        insert_data = {
            "id": account_id,
            "human_email": email,
            "balance": 10.0000,
        }
        if supabase_user_id:
            insert_data["supabase_user_id"] = supabase_user_id
        
        try:
            supabase.table("accounts").insert(insert_data).execute()
            print(f"  ✅ Created account: {account_id} with $10.00 balance")
        except Exception as e:
            print(f"  ❌ Failed to create account: {e}")
            continue

        # 4. Write welcome credit to billing ledger
        try:
            supabase.table("billing_ledger").insert({
                "account_id": account_id,
                "amount": 10.0,
                "balance_after": 10.0,
                "txn_type": "topup",
                "description": "Welcome credit — account fix",
            }).execute()
            print(f"  ✅ Ledger entry created")
        except Exception as e:
            print(f"  ⚠️  Ledger entry failed (non-critical): {e}")

        # 5. Check if they have Razorpay payments that need crediting
        # This would need Razorpay API access — flag for manual review
        print(f"  ⚠️  CHECK: Review Razorpay dashboard for payments from {email}")
        print(f"        If they paid, manually credit their account via:")
        print(f"        UPDATE accounts SET balance = balance + <amount> WHERE id = '{account_id}';")

    print(f"\n{'='*60}")
    print("Done! Users should now see their accounts on the dashboard.")


if __name__ == "__main__":
    fix_users()

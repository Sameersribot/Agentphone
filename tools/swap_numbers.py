"""
Swap phone numbers between two agents (potentially from different accounts).
Also resets their system prompts.

Agent A: agt_bR8x_Bc6IQmyonMT
Agent B: agt_-FTZQsGjyNNDOhPq
"""
import os, sys, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import asyncpg
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

DATABASE_URL = os.getenv("DATABASE_URL")

AGENT_A = "agt_bR8x_Bc6IQmyonMT"
AGENT_B = "agt_-FTZQsGjyNNDOhPq"

async def main():
    conn = await asyncpg.connect(DATABASE_URL)

    # ── Step 1: Show current state ──────────────────────────────
    print("=" * 70)
    print("CURRENT STATE (before swap)")
    print("=" * 70)

    for agent_id in [AGENT_A, AGENT_B]:
        rows = await conn.fetch("""
            SELECT a.id, a.name, a.account_id, a.system_prompt,
                   pn.id AS number_id, pn.phone_number, pn.status
            FROM agents a
            LEFT JOIN phone_numbers pn ON pn.agent_id = a.id AND pn.status = 'active'
            WHERE a.id = $1
        """, agent_id)
        if not rows:
            print(f"\n  WARNING:  Agent {agent_id} NOT FOUND in database!")
            continue
        for row in rows:
            print(f"\n  Agent ID:       {row['id']}")
            print(f"  Agent Name:     {row['name']}")
            print(f"  Account ID:     {row['account_id']}")
            prompt = row['system_prompt'] or ''
            print(f"  System Prompt:  {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
            print(f"  Number ID:      {row['number_id']}")
            print(f"  Phone Number:   {row['phone_number']}")
            print(f"  Number Status:  {row['status']}")

    # ── Step 2: Get the phone number rows for swap ───────────────
    num_a = await conn.fetchrow("""
        SELECT id, phone_number, agent_id, account_id
        FROM phone_numbers
        WHERE agent_id = $1 AND status = 'active'
    """, AGENT_A)

    num_b = await conn.fetchrow("""
        SELECT id, phone_number, agent_id, account_id
        FROM phone_numbers
        WHERE agent_id = $1 AND status = 'active'
    """, AGENT_B)

    if not num_a:
        print(f"\nERROR: No active phone number found for Agent A ({AGENT_A})")
        await conn.close()
        return
    if not num_b:
        print(f"\nERROR: No active phone number found for Agent B ({AGENT_B})")
        await conn.close()
        return

    print(f"\n{'=' * 70}")
    print(f"PLAN: Swap these numbers")
    print(f"{'=' * 70}")
    print(f"  {num_a['phone_number']} (currently on {AGENT_A})  ->  will move to {AGENT_B}")
    print(f"  {num_b['phone_number']} (currently on {AGENT_B})  ->  will move to {AGENT_A}")
    print(f"\n  Both agents' system_prompt will be reset to NULL.")

    confirm = input("\n>>> Proceed with swap? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Aborted.")
        await conn.close()
        return

    # ── Step 3: Perform the swap in a transaction ────────────────
    # Temporarily set agent_id to NULL to avoid unique constraint
    # (idx_one_active_number_per_agent)
    async with conn.transaction():
        # Clear agent assignments
        await conn.execute("UPDATE phone_numbers SET agent_id = NULL WHERE id = $1", num_a['id'])
        await conn.execute("UPDATE phone_numbers SET agent_id = NULL WHERE id = $1", num_b['id'])

        # Swap: assign A's old number to B and vice versa
        # Keep account_id tied to the agent's account
        agent_a_account = num_a['account_id']
        agent_b_account = num_b['account_id']

        await conn.execute(
            "UPDATE phone_numbers SET agent_id = $1, account_id = $2 WHERE id = $3",
            AGENT_B, agent_b_account, num_a['id']
        )
        await conn.execute(
            "UPDATE phone_numbers SET agent_id = $1, account_id = $2 WHERE id = $3",
            AGENT_A, agent_a_account, num_b['id']
        )

        # Reset system prompts
        await conn.execute(
            "UPDATE agents SET system_prompt = NULL WHERE id = $1 OR id = $2",
            AGENT_A, AGENT_B
        )

    # ── Step 4: Verify ───────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print("AFTER SWAP (verification)")
    print("=" * 70)

    for agent_id in [AGENT_A, AGENT_B]:
        rows = await conn.fetch("""
            SELECT a.id, a.name, a.account_id, a.system_prompt,
                   pn.id AS number_id, pn.phone_number, pn.status
            FROM agents a
            LEFT JOIN phone_numbers pn ON pn.agent_id = a.id AND pn.status = 'active'
            WHERE a.id = $1
        """, agent_id)
        for row in rows:
            print(f"\n  Agent ID:       {row['id']}")
            print(f"  Agent Name:     {row['name']}")
            print(f"  Account ID:     {row['account_id']}")
            print(f"  System Prompt:  {row['system_prompt']}")
            print(f"  Number ID:      {row['number_id']}")
            print(f"  Phone Number:   {row['phone_number']}")
            print(f"  Number Status:  {row['status']}")

    print("\nSUCCESS: Swap complete! System prompts reset to NULL.")
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())

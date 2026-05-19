"""
Check billing for account acct_WMH4W8KXaYiHY6Cz
"""
import asyncio
import asyncpg
import os

DATABASE_URL = "postgresql://postgres.chxozazdbgnyedmkmwif:Sameer%40Agentline26@aws-1-us-west-1.pooler.supabase.com:5432/postgres"

async def check():
    db = await asyncpg.connect(DATABASE_URL)

    acct = "acct_WMH4W8KXaYiHY6Cz"

    # 1. Account balance
    row = await db.fetchrow("SELECT id, human_email, balance FROM accounts WHERE id = $1", acct)
    if not row:
        print("Account not found!")
        await db.close()
        return
    print("=== ACCOUNT ===")
    print(f"  ID: {row['id']}")
    print(f"  Email: {row['human_email']}")
    print(f"  Balance: ${float(row['balance']):.4f}")

    # 2. All calls for this account
    calls = await db.fetch("""
        SELECT id, direction, from_number, to_number, status, duration_seconds, started_at, ended_at
        FROM calls WHERE account_id = $1
        ORDER BY started_at DESC LIMIT 20
    """, acct)
    print(f"\n=== CALLS ({len(calls)} found) ===")
    for c in calls:
        dur = c["duration_seconds"] or 0
        print(f"  {c['id']} | {c['direction']:8} | {c['from_number']} -> {c['to_number']} | status={c['status']} | dur={dur}s | {c['started_at']}")

    # 3. All billing ledger entries
    ledger = await db.fetch("""
        SELECT id, amount, balance_after, txn_type, reference_id, description, created_at
        FROM billing_ledger WHERE account_id = $1
        ORDER BY created_at DESC LIMIT 20
    """, acct)
    print(f"\n=== BILLING LEDGER ({len(ledger)} entries) ===")
    for entry in ledger:
        print(f"  #{entry['id']} | {float(entry['amount']):+.4f} | after=${float(entry['balance_after']):.4f} | {entry['txn_type']} | ref={entry['reference_id']} | {entry['description']} | {entry['created_at']}")

    # 4. Check for calls with duration > 0 that have NO matching ledger entry
    unbilled = await db.fetch("""
        SELECT c.id, c.direction, c.duration_seconds, c.status, c.from_number, c.to_number, c.started_at
        FROM calls c
        WHERE c.account_id = $1
          AND c.status = 'completed'
          AND COALESCE(c.duration_seconds, 0) > 0
          AND NOT EXISTS (
            SELECT 1 FROM billing_ledger bl
            WHERE bl.reference_id = c.id AND bl.txn_type = 'call_charge'
          )
        ORDER BY c.started_at DESC
    """, acct)
    print(f"\n=== UNBILLED COMPLETED CALLS ({len(unbilled)} found) ===")
    for c in unbilled:
        dur = c["duration_seconds"] or 0
        print(f"  {c['id']} | {c['direction']:8} | {c['from_number']} -> {c['to_number']} | dur={dur}s | {c['started_at']}")

    # 5. Check for calls with 0 or NULL duration
    zero_dur = await db.fetch("""
        SELECT id, direction, status, duration_seconds, from_number, to_number, started_at, ended_at
        FROM calls
        WHERE account_id = $1
          AND (duration_seconds IS NULL OR duration_seconds = 0)
        ORDER BY started_at DESC
    """, acct)
    print(f"\n=== CALLS WITH 0/NULL DURATION ({len(zero_dur)} found) ===")
    for c in zero_dur:
        print(f"  {c['id']} | {c['direction']:8} | status={c['status']} | dur={c['duration_seconds']} | {c['from_number']} -> {c['to_number']} | started={c['started_at']} | ended={c['ended_at']}")

    await db.close()

asyncio.run(check())

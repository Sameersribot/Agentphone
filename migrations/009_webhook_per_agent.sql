-- Migration 009: Per-agent webhooks (no account-wide)
--
-- Webhooks are strictly per-agent: each agent has at most ONE webhook URL that
-- receives all of that agent's events. There is intentionally NO account-wide
-- webhook, to keep agent event routing unambiguous.

-- Remove any leftover account-wide rows (agent_id NULL) from earlier revisions.
DELETE FROM webhooks WHERE agent_id IS NULL;

-- agent_id is now mandatory.
ALTER TABLE webhooks ALTER COLUMN agent_id SET NOT NULL;

-- Deleting an agent should clean up its webhook (was a plain FK with no action).
ALTER TABLE webhooks DROP CONSTRAINT IF EXISTS webhooks_agent_id_fkey;
ALTER TABLE webhooks
    ADD CONSTRAINT webhooks_agent_id_fkey
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE;

-- Replace any earlier revision's index (singleton or COALESCE per-scope) with
-- the simpler unique (account_id, agent_id).
DROP INDEX IF EXISTS idx_webhooks_one_per_account;
DROP INDEX IF EXISTS idx_webhooks_one_per_scope;
CREATE UNIQUE INDEX IF NOT EXISTS idx_webhooks_one_per_agent
    ON webhooks (account_id, agent_id);

-- Drop the redundant non-unique account index from the original schema.
DROP INDEX IF EXISTS idx_webhooks_account;

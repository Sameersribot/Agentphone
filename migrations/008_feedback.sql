-- Migration 008: Feedback
-- Stores feedback, bug reports, feature requests, and difficulty reports
-- submitted by AI agents via POST /v1/feedback.

CREATE TABLE IF NOT EXISTS feedback (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id      TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    agent_id        TEXT REFERENCES agents(id) ON DELETE SET NULL,
    category        TEXT NOT NULL,                    -- 'bug', 'feature_request', 'difficulty', 'feedback'
    severity        TEXT NOT NULL DEFAULT 'normal',   -- 'low', 'normal', 'high', 'critical'
    subject         TEXT,
    message         TEXT NOT NULL,
    contact_email   TEXT,
    status          TEXT NOT NULL DEFAULT 'open',     -- 'open', 'acknowledged', 'in_progress', 'resolved', 'closed'
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Fast lookups by account, and by status/category for triage
CREATE INDEX IF NOT EXISTS idx_feedback_account  ON feedback(account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_status   ON feedback(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feedback_category ON feedback(category, created_at DESC);

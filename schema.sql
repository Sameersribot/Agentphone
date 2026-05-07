-- AgentLine Database Schema
-- Run via Alembic migration or directly against PostgreSQL

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE accounts (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    human_email      TEXT UNIQUE NOT NULL,
    supabase_user_id TEXT UNIQUE,   -- Links to Supabase Auth user
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE api_keys (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id  TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    key_hash    TEXT NOT NULL,
    key_prefix  TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

CREATE TABLE agents (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id       TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    voice_mode       TEXT DEFAULT 'hosted',
    system_prompt    TEXT,
    initial_greeting TEXT,
    voice_id         TEXT DEFAULT 'cartesia-sonic-english',
    model_tier       TEXT DEFAULT 'balanced',
    transfer_number  TEXT,
    voicemail_message TEXT,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE phone_numbers (
    id             TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id     TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    agent_id       TEXT REFERENCES agents(id),
    provider_id    TEXT UNIQUE NOT NULL,  -- Was telnyx_id, now generic for any provider
    phone_number   TEXT UNIQUE NOT NULL,
    country        TEXT DEFAULT 'US',
    status         TEXT DEFAULT 'active',
    created_at     TIMESTAMPTZ DEFAULT now(),
    released_at    TIMESTAMPTZ
);

CREATE TABLE calls (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id        TEXT REFERENCES accounts(id),
    agent_id          TEXT REFERENCES agents(id),
    number_id         TEXT REFERENCES phone_numbers(id),
    provider_call_id  TEXT,  -- Was telnyx_call_id, now generic
    direction         TEXT NOT NULL,
    from_number       TEXT NOT NULL,
    to_number         TEXT NOT NULL,
    status            TEXT DEFAULT 'initiated',
    system_prompt     TEXT,
    duration_seconds  INTEGER,
    transcript        JSONB DEFAULT '[]',
    started_at        TIMESTAMPTZ DEFAULT now(),
    ended_at          TIMESTAMPTZ
);

CREATE TABLE messages (
    id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id          TEXT REFERENCES accounts(id),
    agent_id            TEXT REFERENCES agents(id),
    number_id           TEXT REFERENCES phone_numbers(id),
    conversation_id     TEXT,
    provider_message_id TEXT,  -- Was telnyx_message_id, now generic
    direction           TEXT NOT NULL,
    from_number         TEXT NOT NULL,
    to_number           TEXT NOT NULL,
    body                TEXT,
    media_url           TEXT,
    status              TEXT DEFAULT 'sent',
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE conversations (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id      TEXT REFERENCES accounts(id),
    agent_id        TEXT REFERENCES agents(id),
    number_id       TEXT REFERENCES phone_numbers(id),
    contact_number  TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    last_message_at TIMESTAMPTZ,
    UNIQUE(number_id, contact_number)
);

CREATE TABLE webhooks (
    id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    agent_id   TEXT REFERENCES agents(id),
    url        TEXT NOT NULL,
    secret     TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);


-- Performance indexes
CREATE INDEX idx_api_keys_prefix ON api_keys(key_prefix) WHERE revoked_at IS NULL;
CREATE INDEX idx_agents_account ON agents(account_id);
CREATE INDEX idx_numbers_agent ON phone_numbers(agent_id) WHERE status = 'active';
CREATE INDEX idx_numbers_phone ON phone_numbers(phone_number);
CREATE INDEX idx_calls_account ON calls(account_id, started_at DESC);
CREATE INDEX idx_calls_agent ON calls(agent_id, started_at DESC);
CREATE INDEX idx_messages_account ON messages(account_id, created_at DESC);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at DESC);
CREATE INDEX idx_conversations_number_contact ON conversations(number_id, contact_number);
CREATE INDEX idx_webhooks_agent ON webhooks(agent_id);
CREATE INDEX idx_webhooks_account ON webhooks(account_id) WHERE agent_id IS NULL;
CREATE INDEX idx_accounts_supabase ON accounts(supabase_user_id) WHERE supabase_user_id IS NOT NULL;

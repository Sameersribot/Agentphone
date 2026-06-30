-- Drop voice_mode from agents.
--
-- Hosted Mode is now the only voice mode (the server runs the conversation).
-- Webhook-driven voice mode (where the agent's own webhook spoke/listened on
-- the call) has been removed. Webhooks remain in use, but solely for real-time
-- event delivery/awareness (see agentline.webhook_dispatcher).
--
-- Companion code changes:
--   * removed agentline/voice/webhook_brain.py
--   * removed voice_mode branching in agentline/voice/pipeline.py
--   * removed voice_mode loading in agentline/routers/signalwire_events.py
--   * removed voice_mode from the agent Pydantic schemas and agents router

ALTER TABLE agents DROP COLUMN IF EXISTS voice_mode;

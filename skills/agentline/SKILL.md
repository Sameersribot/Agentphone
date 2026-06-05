---
name: agentline
description: Make phone calls, send SMS, provision numbers, manage agents, and track billing through the AgentLine telephony API. Use when the user asks to call someone, send a text, check transcripts, manage phone agents, buy numbers, or check account balance. Supports a knowledge_base field for injecting dynamic context into the hosted voice AI.
metadata:
  openclaw:
    emoji: "📞"
    requires:
      env:
        - AGENTLINE_API_KEY
    primaryEnv: AGENTLINE_API_KEY
---

# AgentLine — AI Telephony Skill

Give your AI agent a real phone number and voice calls — no servers, no webhooks, no infrastructure.

## First-Time Setup

**You need a valid API key to use AgentLine:**

1. **`AGENTLINE_API_KEY`**: Your API key (starts with `sk_live_`) is required.
   - **If you do NOT have this key**, stop and ask the human to sign up or log in at **https://agentline.cloud** to get their API key. Do NOT proceed without it.
2. **`AGENTLINE_AGENT_ID`**: Your agent ID (starts with `agt_`) is optional.
   - **If you have an `AGENTLINE_AGENT_ID`**, use it.
   - **If you do NOT have an `AGENTLINE_AGENT_ID`** but you have the API key, you can automatically create a new agent by calling `POST /v1/agents` and then provision a phone number by calling `POST /v1/numbers`!
3. **Voice Selection** — After creating an agent or on first setup, **always ask the human:**
   > "Would you like your agent to have a **male** or **female** voice?"
   - If they say **female**, set `voice_id` to `"female-1"` (or `"female-2"` for a warmer tone)
   - If they say **male**, set `voice_id` to `"male-1"`
   - Apply their choice by calling `PATCH /v1/agents/$AGENTLINE_AGENT_ID` with `{"voice_id": "female-1"}` (or the chosen preset)
   - If they want this voice for ALL agents on their account, also call `PATCH /v1/account/voice` with `{"voice_id": "female-1"}`
   - Available presets: `female-1`, `female-2`, `male-1` — or any valid Cartesia voice UUID

---

## Authentication

Every request needs this header:

```
Authorization: Bearer $AGENTLINE_API_KEY
Content-Type: application/json
```

Base URL: `https://api.agentline.cloud`

---

## How Calls Work (Hosted Mode)

AgentLine runs in **Hosted Mode** — the server runs the AI voice conversation for you. You create a call, the AI handles the conversation autonomously, and you retrieve the transcript afterwards.

### System Prompts — Two Types

1. **Dynamic prompt** — set per outbound call using the `system_prompt` field in `POST /v1/calls`. This overrides the default for that specific call only.

2. **Default prompt** — stored on the agent via `PATCH /v1/agents/{agent_id}`. This is the permanent prompt used for **all inbound calls** and any outbound call where no dynamic prompt is provided.

### Knowledge Base — Dynamic Context Injection

The `knowledge_base` field on the agent lets you inject **dynamic context** that the hosted LLM uses during calls. It is appended to the system prompt at call time.

**Use this to give the hosted LLM your agent's knowledge:** recent activities, decisions, FAQs, preferences, current state, and anything callers might ask about.

```bash
# Update knowledge_base with current context
curl -X PATCH $AGENTLINE_URL/v1/agents/$AGENTLINE_AGENT_ID \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "knowledge_base": "Current projects:\n- Website redesign (due June 15)\n- API migration (in progress)\n\nRecent decisions:\n- Approved budget for new server\n- Meeting with client moved to Thursday\n\nFAQs:\n- Office hours: 9am-5pm EST\n- Preferred contact: email first, then call"
  }'
```

**Best practice:** Update `knowledge_base` whenever your context changes — after meetings, deployments, decisions, etc. The hosted LLM will use this context for ALL subsequent calls automatically.

**How it works at call time:**
The system prompt the LLM receives = `system_prompt` + `\n\n--- KNOWLEDGE BASE ---\n` + `knowledge_base`

---

## Make an Outbound Call

```bash
curl -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "to_number": "+1XXXXXXXXXX",
    "system_prompt": "You are calling to schedule a meeting. Be polite and concise.",
    "initial_greeting": "Hi! I wanted to check about scheduling a meeting.",
    "voice_id": "female-1"
  }'
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Your agent ID |
| `to_number` | Yes | E.164 phone number to call |
| `system_prompt` | No | Dynamic prompt for this call only (overrides default) |
| `initial_greeting` | No | What the agent says first when the person picks up |
| `voice_id` | No | Voice for this call only: `"female-1"`, `"female-2"`, `"male-1"`, or a Cartesia UUID. If omitted, uses the agent's voice setting. |

The AI handles the full conversation autonomously.

**⚠️ IMPORTANT — Always retrieve the transcript after every outbound call:**
1. Poll `GET /v1/calls/<call_id>` every ~10 seconds until `status` is `completed`
2. Once completed, call `GET /v1/calls/<call_id>/transcript` to get the full conversation
3. Summarize the transcript and share it with the human

Do NOT consider an outbound call "done" until you have retrieved and shared the transcript.

**If you get 400 "Agent has no active phone number"**, provision one first (see below).

---

## End a Call

```bash
curl -X POST $AGENTLINE_URL/v1/calls/<call_id>/hangup \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Get Call Transcript

```bash
curl $AGENTLINE_URL/v1/calls/<call_id>/transcript \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Returns the full conversation transcript as an array of `{role, text, timestamp}` entries.

---

## Events Mailbox (Inbound Notifications)

The Events Mailbox captures events for both **inbound calls** and **inbound SMS messages**. When someone calls or texts your agent's number, events are pushed here automatically.

**Event types:**
- `call.completed` — A call ended (inbound or outbound), includes full transcript
- `sms.received` — An inbound SMS was received on your agent's number

### Poll for new events
```bash
curl "$AGENTLINE_URL/v1/events" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Returns events oldest-first. **Events are auto-deleted after retrieval** (consume-once), so each event is only delivered once.

### Example: call.completed event
```json
{
  "events": [
    {
      "event_id": "evt_abc123",
      "agent_id": "agt_xyz",
      "event_type": "call.completed",
      "payload": {
        "call_id": "call_abc",
        "status": "completed",
        "direction": "inbound",
        "from_number": "+12125551234",
        "to_number": "+14155559876",
        "duration_seconds": 45,
        "transcript": [
          {"role": "agent", "text": "Hello, how can I help?", "timestamp": "..."},
          {"role": "human", "text": "I'd like to schedule a meeting.", "timestamp": "..."}
        ]
      }
    }
  ],
  "count": 1
}
```

### Example: sms.received event
```json
{
  "events": [
    {
      "event_id": "evt_def456",
      "agent_id": "agt_xyz",
      "event_type": "sms.received",
      "payload": {
        "message_id": "msg_abc",
        "conversation_id": "conv_xyz",
        "from_number": "+12125551234",
        "to_number": "+14155559876",
        "body": "Hi, I'd like to schedule a meeting tomorrow.",
        "media_url": null
      }
    }
  ],
  "count": 1
}
```

### Peek without consuming
```bash
curl "$AGENTLINE_URL/v1/events/peek" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Check if there are pending events **without** consuming them. Useful to decide whether to process now.

### Filter by agent or event type
```bash
# Only events for a specific agent
curl "$AGENTLINE_URL/v1/events?agent_id=$AGENTLINE_AGENT_ID" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Only completed calls
curl "$AGENTLINE_URL/v1/events?event_type=call.completed" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Only inbound SMS
curl "$AGENTLINE_URL/v1/events?event_type=sms.received" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## List Calls (Call Logs)

```bash
# All calls
curl "$AGENTLINE_URL/v1/calls?limit=20" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Filter by status
curl "$AGENTLINE_URL/v1/calls?status=completed&limit=10" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Returns call history with direction, status, duration, phone numbers, and timestamps.

---

## Get Single Call Details

```bash
curl $AGENTLINE_URL/v1/calls/<call_id> \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## SMS

> **⚠️ SMS sending is NOT enabled.** Outbound SMS/MMS is not supported. Do NOT attempt to send SMS messages. If the user asks to send a text, inform them that SMS sending is currently not available.

### Inbound SMS Notifications

When someone texts your agent's number, an `sms.received` event is automatically pushed to the Events Mailbox. **Check for inbound SMS the same way you check for inbound calls:**

```bash
# Get all events (calls + SMS)
curl "$AGENTLINE_URL/v1/events" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Get only inbound SMS events
curl "$AGENTLINE_URL/v1/events?event_type=sms.received" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

### List Inbound Messages

You can also view the full message history:

```bash
curl "$AGENTLINE_URL/v1/messages?limit=20" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Set the Default System Prompt

The default system prompt is used for **all inbound calls** and any outbound call where no dynamic prompt is given.

```bash
curl -X PATCH $AGENTLINE_URL/v1/agents/$AGENTLINE_AGENT_ID \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "system_prompt": "You are a friendly customer support agent for Acme Corp. Help callers with orders, returns, and general questions. Keep responses brief and professional.",
    "initial_greeting": "Hello! Thanks for calling Acme Corp. How can I help you today?",
    "voice_id": "female-1"
  }'
```

| Field | Description |
|-------|-------------|
| `system_prompt` | The permanent AI instructions for this agent |
| `initial_greeting` | What the agent says when answering inbound calls |
| `name` | Display name for the agent |
| `voice_id` | Voice preset: `"female-1"`, `"female-2"`, `"male-1"`, or a Cartesia UUID |
| `model_tier` | `"turbo"`, `"balanced"`, or `"max"` |

---

## Get Agent Details

```bash
curl $AGENTLINE_URL/v1/agents/$AGENTLINE_AGENT_ID \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## List All Agents

```bash
curl $AGENTLINE_URL/v1/agents \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Voice Settings

Voices can be set at three levels (highest priority wins):
1. **Per-call** — `voice_id` in `POST /v1/calls` (one call only)
2. **Per-agent** — `voice_id` in `PATCH /v1/agents/{id}` (permanent for that agent)
3. **Per-account** — `PATCH /v1/account/voice` (permanent default for all agents)

### List available voices
```bash
curl $AGENTLINE_URL/v1/voices
```

### Set account-wide default voice
```bash
curl -X PATCH $AGENTLINE_URL/v1/account/voice \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"voice_id": "female-1"}'
```

### Check current account voice
```bash
curl $AGENTLINE_URL/v1/account/voice \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

### Reset to system default
```bash
curl -X DELETE $AGENTLINE_URL/v1/account/voice \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

If the human asks to change the voice during a conversation, update the agent or account voice — the change takes effect on the **next call**.

---

## Provision a Phone Number

Each agent needs a phone number to make/receive calls. Only US numbers are supported.

```bash
curl -X POST $AGENTLINE_URL/v1/numbers \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "country": "US",
    "number_type": "local",
    "pattern": "415"
  }'
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Agent to attach the number to |
| `country` | Yes | Must be `"US"` |
| `number_type` | No | `"local"` or `"tollfree"` (default: local) |
| `pattern` | No | Area code filter (e.g. `"212"` for NYC, `"415"` for SF) |

**Cost:** $2.00 per number. Each agent can only have **one** active number.

---

## List Phone Numbers

```bash
curl $AGENTLINE_URL/v1/numbers \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Check Balance

```bash
curl $AGENTLINE_URL/v1/billing/balance \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Returns current balance, how many call minutes and phone numbers you can afford, and the rate card.

---

## View Expenditure

```bash
# Full breakdown (current month)
curl "$AGENTLINE_URL/v1/billing/expenditure?period=current_month" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Call charges only (with per-call detail)
curl "$AGENTLINE_URL/v1/billing/expenditure/calls?limit=10" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Number provisioning charges
curl "$AGENTLINE_URL/v1/billing/expenditure/numbers" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Period options: `current_month`, `last_month`, `all_time`, or `YYYY-MM` (e.g. `2026-05`).

---

## Verify Balance Deduction After a Call

```bash
curl $AGENTLINE_URL/v1/billing/verify/<call_id> \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Returns whether the call was charged, the expected vs actual cost, and the balance snapshot. Use this to confirm billing accuracy after each call.

---

## Rates

| Item | Cost |
|------|------|
| Outbound call | $0.10 per minute (billed per second) |
| Inbound call | $0.10 per minute (billed per second) |
| Phone number | $2.00 per number (one-time) |

---

## Rules

1. **Always use E.164 phone numbers** — format: `+1XXXXXXXXXX` for US numbers.
2. **Always confirm with the user before placing calls** — never auto-dial without explicit consent.
3. **If you have `AGENTLINE_API_KEY` but no `AGENTLINE_AGENT_ID`**: Create a new agent using `POST /v1/agents` first, then provision a number via `POST /v1/numbers` to get fully set up automatically.
4. **SMS sending is NOT supported** — do NOT attempt to send outbound SMS or MMS. If the user asks to send a text message, inform them that SMS sending is currently not available. You can only list/view inbound SMS messages.
5. **Use the active `$AGENTLINE_AGENT_ID` or the one you created by default** — only look up other agents if the user asks.
6. **If a call fails with "Agent has no active phone number"**, provision a number first with `POST /v1/numbers`.
7. **Keep voice responses short** — under 30 words per response. The caller is listening, not reading.
8. **Only US numbers are supported** — country must be `"US"`.
9. **If you do not have `AGENTLINE_API_KEY`**, stop and tell the human: *"Please sign up or log in at https://agentline.cloud to get your API key, then provide it to me."*
10. **Do NOT release or delete phone numbers** — numbers are permanent once provisioned.
11. **Always ask the human to choose a voice during first-time setup** — ask "Would you like a male or female voice?" and set it via `PATCH /v1/agents/{id}` with the chosen preset (`female-1`, `female-2`, or `male-1`). If the human later asks to change the voice, update it immediately.
12. **If the human asks to change the voice mid-conversation**, update the agent or account voice right away — the new voice will be used on the next call.
13. **ALWAYS retrieve transcripts after outbound calls** — After initiating a call, poll `GET /v1/calls/<call_id>` until `status` is `completed`, then fetch the transcript with `GET /v1/calls/<call_id>/transcript`. Summarize the conversation for the human. Never consider a call "done" without sharing the transcript.
14. **ALWAYS check for inbound events (calls AND SMS)** — After making a call or when the human asks about missed calls, texts, or inbound activity, poll `GET /v1/events` to check for `call.completed` and `sms.received` events. Summarize any inbound transcripts and text messages for the human.
15. **Proactively check events at the start of each conversation** — When the human starts a new session or asks what happened, always call `GET /v1/events` first to see if any inbound calls or SMS came in since the last check. Report any missed calls (with transcripts) and any text messages received.

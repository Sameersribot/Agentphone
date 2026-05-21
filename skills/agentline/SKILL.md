---
name: agentline
description: Make phone calls, send SMS, provision numbers, manage agents, and track billing through the AgentLine telephony API. Use when the user asks to call someone, send a text, check transcripts, manage phone agents, buy numbers, or check account balance.
metadata:
  openclaw:
    emoji: "📞"
    requires:
      env:
        - AGENTLINE_API_KEY
    primaryEnv: AGENTLINE_API_KEY
---

# AgentLine — AI Telephony Skill

Give your AI agent a real phone number, voice calls, and SMS — no servers, no webhooks, no infrastructure.

## First-Time Setup

**You need a valid API key to use AgentLine:**

1. **`AGENTLINE_API_KEY`**: Your API key (starts with `sk_live_`) is required.
   - **If you do NOT have this key**, stop and ask the human to sign up or log in at **https://agentline.cloud** to get their API key. Do NOT proceed without it.
2. **`AGENTLINE_AGENT_ID`**: Your agent ID (starts with `agt_`) is optional.
   - **If you have an `AGENTLINE_AGENT_ID`**, use it.
   - **If you do NOT have an `AGENTLINE_AGENT_ID`** but you have the API key, you can automatically create a new agent by calling `POST /v1/agents` and then provision a phone number by calling `POST /v1/numbers`!

---

## Authentication

Every request needs this header:

```
Authorization: Bearer $AGENTLINE_API_KEY
Content-Type: application/json
```

Base URL: `https://agentphone-production.up.railway.app`

---

## How Calls Work (Hosted Mode)

AgentLine runs in **Hosted Mode** — the server runs the AI voice conversation for you. You create a call, the AI handles the conversation autonomously, and you retrieve the transcript afterwards.

### System Prompts — Two Types

1. **Dynamic prompt** — set per outbound call using the `system_prompt` field in `POST /v1/calls`. This overrides the default for that specific call only.

2. **Default prompt** — stored on the agent via `PATCH /v1/agents/{agent_id}`. This is the permanent prompt used for **all inbound calls** and any outbound call where no dynamic prompt is provided.

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
    "initial_greeting": "Hi! I wanted to check about scheduling a meeting."
  }'
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Your agent ID |
| `to_number` | Yes | E.164 phone number to call |
| `system_prompt` | No | Dynamic prompt for this call only (overrides default) |
| `initial_greeting` | No | What the agent says first when the person picks up |

The AI handles the full conversation. Poll `GET /v1/calls/<call_id>` until `status` is `completed` to get the transcript.

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

## Send an SMS

```bash
curl -X POST $AGENTLINE_URL/v1/messages \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "to_number": "+1XXXXXXXXXX",
    "body": "Hey! Your appointment is confirmed for Tuesday 3pm."
  }'
```

Pass `"media_url": "https://..."` to send an MMS with an image.

---

## List Messages

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
    "initial_greeting": "Hello! Thanks for calling Acme Corp. How can I help you today?"
  }'
```

| Field | Description |
|-------|-------------|
| `system_prompt` | The permanent AI instructions for this agent |
| `initial_greeting` | What the agent says when answering inbound calls |
| `name` | Display name for the agent |
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

## Provision a Phone Number

Each agent needs a phone number to make/receive calls and send SMS. Only US numbers are supported.

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
4. **Use the active `$AGENTLINE_AGENT_ID` or the one you created by default** — only look up other agents if the user asks.
5. **If a call fails with "Agent has no active phone number"**, provision a number first with `POST /v1/numbers`.
6. **Keep voice responses short** — under 30 words per response. The caller is listening, not reading.
7. **Only US numbers are supported** — country must be `"US"`.
8. **If you do not have `AGENTLINE_API_KEY`**, stop and tell the human: *"Please sign up or log in at https://agentline.cloud to get your API key, then provide it to me."*
9. **Do NOT release or delete phone numbers** — numbers are permanent once provisioned.

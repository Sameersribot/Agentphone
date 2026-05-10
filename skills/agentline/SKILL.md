---
name: agentline
description: Make phone calls and send SMS messages through the AgentLine telephony API. Use when the user asks to call someone, send a text message, check call transcripts, manage phone agents, or provision phone numbers.
metadata:
  openclaw:
    emoji: "📞"
    requires:
      env:
        - AGENTLINE_API_KEY
        - AGENTLINE_AGENT_ID
    primaryEnv: AGENTLINE_API_KEY
---

# AgentLine — Phone Call & SMS Skill

You can make phone calls and send SMS messages through the AgentLine API.
AgentLine manages real phone numbers, voice agents, and SMS conversations.

## Authentication

Every request needs these headers:

```
Authorization: Bearer $AGENTLINE_API_KEY
Content-Type: application/json
```

Base URL: `https://agentphone-production.up.railway.app`

## Default Agent

Your default agent ID is `$AGENTLINE_AGENT_ID`. Use this for all calls and SMS unless the user specifies a different agent.

---

## Voice Modes

AgentLine supports three voice modes:

### 1. Hosted Mode (default for outbound calls)
The server runs the LLM using the agent's `system_prompt`. No webhook or external server needed. Just create the call and the AI handles everything.

### 2. Relay Mode (agent controls the conversation — **recommended for AI agents**)
Your agent controls the conversation via API. No webhook needed, no public URL needed.
This works perfectly from localhost, behind firewalls, or anywhere.

**Flow:**
1. Create a call → `POST /v1/calls`
2. Poll for events → `GET /v1/events?agent_id=$AGENTLINE_AGENT_ID&wait=true`
3. When you receive a `call.speech_received` event, process with your LLM
4. Send response → `POST /v1/calls/{call_id}/speak`
5. Repeat until call ends

### 3. Webhook Mode (external server)
Speech is transcribed and POSTed to a configured webhook URL. Only use this if you have a public server. **Not needed for most agents.**

---

## Voice Call — How It Works

### Outbound Call (Hosted Mode — simplest)
Just create the call with a `system_prompt`. The AI runs the conversation autonomously.

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

The AI handles the full conversation. Poll `GET /v1/calls/<call_id>` until `status` is `completed` to get the transcript.

### Relay Mode (your LLM controls the conversation — NO public URL needed)

**Step 1: Create the call**
```bash
curl -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "to_number": "+1XXXXXXXXXX"
  }'
# Returns: {"id": "call_xxx", ...}
```

**Step 2: Poll for events (long-poll, holds 25 seconds)**
```bash
curl "$AGENTLINE_URL/v1/events?agent_id=$AGENTLINE_AGENT_ID&wait=true" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Returns events like:
```json
{
  "events": [
    {
      "event_id": "evt_xxx",
      "event_type": "call.speech_received",
      "data": {
        "call_id": "call_xxx",
        "speech_text": "Hello, who is this?",
        "from_number": "+14155551234",
        "direction": "outbound"
      }
    }
  ],
  "pending": 1
}
```

**Step 3: Send your response**
```bash
curl -X POST $AGENTLINE_URL/v1/calls/call_xxx/speak \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hi! This is the scheduling assistant."}'
```

**Step 4: Acknowledge events you've processed**
```bash
curl -X POST $AGENTLINE_URL/v1/events/ack \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"event_ids": ["evt_xxx"]}'
```

**Step 5: Loop back to Step 2 until call ends**

That's it. No webhook URL, no ngrok, no public server needed.

---

## Events API (Mailbox Mode)

Every agent automatically has an event mailbox. All events (speech, SMS, call status) are queued server-side and you pull them via API.

### Poll for Events
```bash
# Instant (returns immediately)
curl "$AGENTLINE_URL/v1/events?agent_id=$AGENTLINE_AGENT_ID" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Long-poll (holds up to 25 seconds for new events)
curl "$AGENTLINE_URL/v1/events?agent_id=$AGENTLINE_AGENT_ID&wait=true" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

### Event Types
| Event | When | Key Fields |
|-------|------|------------|
| `call.speech_received` | Person spoke on a call | `call_id`, `speech_text` |
| `call.inbound` | Someone called your number | `call_id`, `from_number` |
| `call.completed` | Call ended | `call_id`, `duration` |
| `agent.message` | Inbound SMS/MMS | `from_number`, `content` |

### Acknowledge Events
```bash
curl -X POST $AGENTLINE_URL/v1/events/ack \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"event_ids": ["evt_xxx", "evt_yyy"]}'
```

### Clear All Events
```bash
curl -X DELETE "$AGENTLINE_URL/v1/events?agent_id=$AGENTLINE_AGENT_ID" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Outbound Call Fields

```bash
curl -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "to_number": "+1XXXXXXXXXX",
    "system_prompt": "You are a helpful assistant.",
    "initial_greeting": "Hello! How can I help?"
  }'
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Agent making the call |
| `to_number` | Yes | E.164 phone number |
| `system_prompt` | No | Override agent's default prompt |
| `initial_greeting` | No | What the agent says first |
| `model_tier` | No | `"turbo"`, `"balanced"`, or `"max"` |

---

## End a Call

```bash
curl -X POST $AGENTLINE_URL/v1/calls/<call_id>/hangup \
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

---

## Get Call Transcript

```bash
curl $AGENTLINE_URL/v1/calls/<call_id>/transcript \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## List Calls

```bash
curl "$AGENTLINE_URL/v1/calls?limit=10" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## List Agents

```bash
curl $AGENTLINE_URL/v1/agents \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## List Phone Numbers

```bash
curl $AGENTLINE_URL/v1/numbers \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Provision a Phone Number

```bash
curl -X POST $AGENTLINE_URL/v1/numbers \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "country": "US",
    "area_code": "415"
  }'
```

Note: Each agent can only have ONE active phone number.

---

## Webhook Mode (optional — only if you have a public server)

If you have a public server and prefer push-based delivery, you can configure a webhook:

```bash
curl -X POST $AGENTLINE_URL/v1/webhooks \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-public-server.com/voice-webhook", "agent_id": "$AGENTLINE_AGENT_ID"}'
```

**Note:** If no webhook is configured, events are automatically queued in the event mailbox. You don't need to configure anything — just use `GET /v1/events`.

---

## Legacy API (still works)

The `/listen` and `/speak` polling endpoints still work for backwards compatibility, but the events API is preferred for cleaner integration.

---

## Rules

1. **Always use E.164 phone numbers** — format: `+1XXXXXXXXXX` (US), `+91XXXXXXXXXX` (India).
2. **Always confirm with the user before placing calls** — never auto-dial without explicit consent.
3. **Use `$AGENTLINE_AGENT_ID` by default** — only look up agents if the user asks to manage them.
4. **For hosted mode** — just create the call with a system_prompt. The AI handles everything.
5. **For relay mode** — poll `GET /v1/events?wait=true`, respond with `/speak`. No webhook needed.
6. **Keep voice responses short** — under 30 words per response. The caller is listening, not reading.
7. **If a call fails with 400 "Agent has no active phone number"**, provision a number first.
8. **SMS supports MMS** — pass `media_url` to attach images.
9. **Events auto-expire after 5 minutes** — always poll regularly during active calls.

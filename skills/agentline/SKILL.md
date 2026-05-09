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

AgentLine supports two voice modes, just like AgentPhone:

### 1. Hosted Mode (default for outbound calls)
The server runs the LLM using the agent's `system_prompt`. No webhook or external server needed. Just create the call and the AI handles everything.

### 2. Webhook Mode
Speech is transcribed in real-time and POSTed to your configured webhook URL as `agent.message` events with `channel: "voice"`. Your server processes with your own LLM and returns `{"text": "response text"}` in the HTTP response body. AgentLine speaks that text to the caller.

This is the same pattern as AgentPhone — **one HTTP round-trip per conversation turn**.

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

### Webhook Mode (your LLM controls the conversation)
If you have a webhook configured, speech is sent there instead. Your webhook receives:

```json
{
  "event": "agent.message",
  "channel": "voice",
  "agentId": "agt_...",
  "callId": "call_...",
  "data": {
    "transcript": "Hello, who is this?",
    "fromNumber": "+14155551234",
    "toNumber": "+14155559999",
    "direction": "outbound"
  },
  "recentHistory": [
    {"direction": "outbound", "content": "Hi! How can I help?", "timestamp": "..."},
    {"direction": "inbound", "content": "Hello, who is this?", "timestamp": "..."}
  ]
}
```

Your webhook must return:
```json
{"text": "Hi! This is the scheduling assistant. I wanted to check about a meeting for Tuesday."}
```

That text is spoken to the caller immediately. **No polling, no /speak call — just return the response in the webhook body.**

### Configure a Webhook
```bash
curl -X POST $AGENTLINE_URL/v1/webhooks \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-server.com/voice-webhook", "agent_id": "$AGENTLINE_AGENT_ID"}'
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

Or from your webhook, return: `{"text": "Goodbye!", "hangup": true}`

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

## Legacy API (still works)

The `/listen` and `/speak` polling endpoints still work for backwards compatibility, but the webhook-response pattern is preferred for lower latency.

---

## Rules

1. **Always use E.164 phone numbers** — format: `+1XXXXXXXXXX` (US), `+91XXXXXXXXXX` (India).
2. **Always confirm with the user before placing calls** — never auto-dial without explicit consent.
3. **Use `$AGENTLINE_AGENT_ID` by default** — only look up agents if the user asks to manage them.
4. **For hosted mode** — just create the call with a system_prompt. The AI handles everything.
5. **For webhook mode** — configure a webhook, return `{"text": "..."}` in the response body. That's it.
6. **Keep voice responses short** — under 30 words per response. The caller is listening, not reading.
7. **If a call fails with 400 "Agent has no active phone number"**, provision a number first.
8. **SMS supports MMS** — pass `media_url` to attach images.

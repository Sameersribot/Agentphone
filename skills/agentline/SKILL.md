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
AgentLine manages real phone numbers, voice agents (STT → LLM → TTS), and SMS conversations via Plivo.

## Authentication

Every request needs these headers:

```
Authorization: Bearer $AGENTLINE_API_KEY
Content-Type: application/json
```

Base URL: `https://skeletal-surely-henna.ngrok-free.app` (or the deployed AgentLine URL)

## Default Agent

Your default agent ID is `$AGENTLINE_AGENT_ID`. Use this for all calls and SMS unless the user specifies a different agent. You do not need to look up agents before making a call or sending a message — just use this ID directly.

---

## 1. Place an Outbound Call

The AI agent will autonomously handle the entire phone conversation using voice (Deepgram STT → GPT-4o → Cartesia TTS).

```bash
curl -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "to_number": "+1XXXXXXXXXX",
    "system_prompt": "You are calling to schedule a meeting for Tuesday at 3pm. Be polite and concise.",
    "model_tier": "balanced"
  }'
```

**Required fields:**
- `agent_id` — the agent making the call (get from `GET /v1/agents`)
- `to_number` — E.164 format phone number (e.g. `+14155551234`)

**Optional fields:**
- `system_prompt` — override the agent's default prompt for this call
- `initial_greeting` — what the agent says first
- `voice` — voice ID override
- `model_tier` — `"turbo"` (fast), `"balanced"` (default), or `"max"` (highest quality)
- `from_number_id` — use a specific number if the agent has multiple

**Response:**
```json
{
  "id": "call_aBcDeFgHiJkLmN",
  "agent_id": "agt_...",
  "from_number": "+12125551234",
  "to_number": "+14155559876",
  "direction": "outbound",
  "status": "in-progress",
  "started_at": "2026-05-06T12:00:00Z"
}
```

---

## 2. Send an SMS

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

**Required fields:**
- `agent_id` — the agent sending the message
- `to_number` — E.164 format
- `body` — the message text

**Optional fields:**
- `media_url` — attach an image/file (MMS)
- `from_number_id` — use a specific number

**Response:**
```json
{
  "id": "msg_aBcDeFgHiJkLmN",
  "conversation_id": "conv_...",
  "agent_id": "agt_...",
  "from_number": "+12125551234",
  "to_number": "+14155559876",
  "body": "Hey! Your appointment is confirmed for Tuesday 3pm.",
  "direction": "outbound",
  "status": "sent"
}
```

---

## 3. Get Call Transcript

After a call completes, retrieve what was said:

```bash
curl $AGENTLINE_URL/v1/calls/<call_id>/transcript \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

**Response:**
```json
{
  "call_id": "call_...",
  "status": "completed",
  "transcript": [
    {"role": "agent", "text": "Hi! I'm calling to schedule a meeting.", "timestamp": "00:02"},
    {"role": "user", "text": "Sure, Tuesday works for me.", "timestamp": "00:05"}
  ]
}
```

---

## 4. List Calls

```bash
curl "$AGENTLINE_URL/v1/calls?limit=10" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Optional query params: `agent_id`, `status` (initiated, in-progress, completed, failed), `limit`, `offset`

---

## 5. Get Call Details

```bash
curl $AGENTLINE_URL/v1/calls/<call_id> \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## 6. List SMS Conversations

```bash
curl "$AGENTLINE_URL/v1/messages/conversations" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Optional query param: `agent_id`

---

## 7. List Messages

```bash
curl "$AGENTLINE_URL/v1/messages?limit=20" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

Optional query params: `agent_id`, `conversation_id`, `limit`, `offset`

---

## 8. List Agents

```bash
curl $AGENTLINE_URL/v1/agents \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## 9. Create an Agent

```bash
curl -X POST $AGENTLINE_URL/v1/agents \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Sales Agent",
    "voice_mode": "hosted",
    "system_prompt": "You are a helpful sales representative. Keep responses under 30 words.",
    "initial_greeting": "Hi there! How can I help you today?",
    "voice_id": "cartesia-sonic-english",
    "model_tier": "balanced"
  }'
```

**voice_mode options:** `"hosted"` (AgentLine handles LLM) or `"webhook"` (external LLM)

---

## 10. List Phone Numbers

```bash
curl $AGENTLINE_URL/v1/numbers \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## 11. Provision a Phone Number

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

---

## Rules

1. **Always use E.164 phone numbers** — format: `+1XXXXXXXXXX` (US), `+44XXXXXXXXXX` (UK), etc.
2. **Always confirm with the user before placing calls or sending SMS** — never auto-dial without explicit consent.
3. **Use `$AGENTLINE_AGENT_ID` by default** — it's your preconfigured agent. Only use `GET /v1/agents` if the user asks to switch agents or manage multiple agents.
4. **After placing a call**, tell the user they can check the transcript once the call completes.
5. **Keep system_prompt instructions concise for voice calls** — the agent speaks the responses aloud, so shorter is better.
6. **Use `exec` tool** to run the curl commands above. Replace `$AGENTLINE_URL` with the base URL and `$AGENTLINE_API_KEY` with the key from your environment.
7. **If a call fails with 400 "Agent has no active phone number"**, the user needs to provision a number first using endpoint 11.
8. **SMS supports MMS** — pass `media_url` to attach images.

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

## Voice Call — The Conversation Loop

This is the most important section. To have a real-time voice conversation, you must run a fast loop:

### Step 1: Create the call
```bash
curl -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "$AGENTLINE_AGENT_ID",
    "to_number": "+1XXXXXXXXXX",
    "system_prompt": "You are calling to schedule a meeting. Be polite and concise."
  }'
```
Save the `id` from the response — this is your `call_id`.

### Step 2: Listen for caller's speech
```bash
curl "$AGENTLINE_URL/v1/calls/<call_id>/listen?wait=true&after=0" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```
- `wait=true` — holds the connection until new speech arrives (up to 25 seconds)
- `after=0` — only return transcript entries after this index

This returns the caller's speech as a transcript array. Extract the latest human speech.

### Step 3: Respond immediately
```bash
curl -X POST $AGENTLINE_URL/v1/calls/<call_id>/speak \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Sure, Tuesday at 3pm works. I will send you a confirmation."}'
```

### Step 4: Loop back to Step 2
After `/speak`, immediately go back to `/listen` with `after` set to the latest transcript index. Continue until the conversation is done.

### Step 5: End the call
```bash
curl -X POST $AGENTLINE_URL/v1/calls/<call_id>/hangup \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

### ⚡ CRITICAL: Speed Requirements

> **You have 15 seconds to respond via /speak after the caller finishes talking.**
> If you don't respond in 15 seconds, a fallback LLM will answer for you.
> Aim for under 5 seconds: receive speech → process → call /speak.

The voice pipeline works like this:
1. Caller speaks → real-time transcription (instant)
2. Transcript saved to DB → your `/listen` returns it
3. **You must call `/speak` within 15 seconds**
4. AgentLine speaks your text to the caller → listens for next speech → loop

### Example Conversation Loop (pseudocode)
```
call = POST /v1/calls {agent_id, to_number}
after = 0

while call is active:
    transcript = GET /v1/calls/{call.id}/listen?wait=true&after={after}
    latest_speech = extract latest human entry from transcript
    after = len(transcript)

    response = YOUR_LLM.generate(latest_speech)  # Your agent's brain
    POST /v1/calls/{call.id}/speak {text: response}

POST /v1/calls/{call.id}/hangup
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

Optional query params: `agent_id`, `status` (initiated, in-progress, completed, failed), `limit`, `offset`

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

## Rules

1. **Always use E.164 phone numbers** — format: `+1XXXXXXXXXX` (US), `+91XXXXXXXXXX` (India).
2. **Always confirm with the user before placing calls** — never auto-dial without explicit consent.
3. **Use `$AGENTLINE_AGENT_ID` by default** — only look up agents if the user asks to manage them.
4. **Be FAST during voice calls** — you have 15 seconds to respond via `/speak`. Process speech and respond as quickly as possible.
5. **Run the conversation loop** — don't just create a call and forget it. Run `/listen` → process → `/speak` → loop until done.
6. **End the call when done** — call `/hangup` when the conversation is finished.
7. **Keep voice responses short** — under 30 words per response. The caller is listening, not reading.
8. **If a call fails with 400 "Agent has no active phone number"**, provision a number first.
9. **SMS supports MMS** — pass `media_url` to attach images.

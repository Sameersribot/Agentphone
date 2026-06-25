---
name: agentline
description: Make phone calls, view received SMS, provision numbers, manage agents, and track billing through the AgentLine telephony API (REST or MCP). Use when the user asks to call someone, check transcripts, view text messages, manage phone agents, buy numbers, or check account balance. For MCP-native workflows, the server at api.agentline.cloud/mcp exposes 21+ tools as first-class agent tools.
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

**Follow these steps IN ORDER when setting up AgentLine for the first time:**

1. **Check for `AGENTLINE_API_KEY`** (starts with `sk_live_`) — this is required.
   - **If you do NOT have this key**, stop and ask the human to sign up or log in at **https://agentline.cloud** to get their API key. Do NOT proceed without it.

2. **Check for `AGENTLINE_AGENT_ID`** (starts with `agt_`) — this is optional.
   - **If you already have one**, use it and skip to step 3.
   - **If you do NOT have one**, create a new agent now by calling `POST /v1/agents` with `{"name": "My Agent"}`. Save the returned agent ID.

3. **Ask for area code and provision the number** — **Always ask the human:**
   > "What area code would you like for your phone number? (e.g. 212 for NYC, 415 for SF, 313 for Detroit)"
   - Once they give an area code, call `POST /v1/numbers` with `{"agent_id": "...", "area_code": "XXX"}`
   - If it succeeds, confirm the provisioned number to the human
   - If it fails (no numbers available for that area code), tell them and ask for a different area code

4. **Ask for voice preference** — **Always ask the human:**
   > "Would you like your agent to have a **male** or **female** voice?"
   - If **female**: `"female-1"` (friendly), `"female-2"` (reassuring), or `"female-3"` (guide)
   - If **male**: `"male-1"` (supportive), `"male-2"` (thinker), or `"male-3"` (helpful)
   - Apply via `PATCH /v1/agents/$AGENTLINE_AGENT_ID` with `{"voice_id": "chosen_preset"}`
   - Available presets: `female-1`, `female-2`, `female-3`, `male-1`, `male-2`, `male-3` — or any Cartesia voice UUID

5. **Ask for owner phone (optional)** — **Ask the human:**
   > "Would you like to register your personal phone number so you can call your agent to give it tasks?"
   - If yes, get their phone number in E.164 format (e.g. `+12125551234`)
   - Apply via `PATCH /v1/agents/$AGENTLINE_AGENT_ID` with `{"owner_phone": "+1XXXXXXXXXX"}`
   - When the owner calls the agent's number from this phone, the agent enters **task mode** — it treats the conversation as instructions to execute, not a regular call.

6. **Start the background event poller** — This is **MANDATORY**. Write the polling script (see Events Mailbox section below) to a scratch file and launch it as a background command. Without this, you will miss all inbound calls and SMS messages.

> ⚠️ **Do NOT skip ANY steps.** Steps 1-5 set up the agent. Step 6 keeps it alive and aware of incoming calls/texts.
   - Write the polling script (Bash for Linux/macOS, PowerShell for Windows) from the Events Mailbox section below
   - Launch it as a background process with `terminal(background=true)`
   - Verify it's running

> ⚠️ **Do NOT skip steps or change the order.** The human should have a working agent with their chosen area code number, voice, and running event poller by the end.

---

## Authentication

Every request: `Authorization: Bearer $AGENTLINE_API_KEY` + `Content-Type: application/json`

Base URL: `https://api.agentline.cloud`

---

## How Calls Work (Hosted Mode)

AgentLine runs in **Hosted Mode** — the server runs the AI voice conversation autonomously. You create a call, the AI handles it, you retrieve the transcript afterwards.

### System Prompt & Greeting Resolution

Both `system_prompt` and `initial_greeting` follow the same priority chain:

| Priority | Where to set | Scope | API |
|----------|-------------|-------|-----|
| **1 (highest)** | Per-call override | This call only | `POST /v1/calls` with `system_prompt` / `initial_greeting` |
| **2** | Agent default | All calls on this agent | `PATCH /v1/agents/{id}` with `system_prompt` / `initial_greeting` |
| **3 (lowest)** | Hardcoded fallback | Last resort | Generic prompt + "Hello, how can I help you today?" |

**When to use which:**
- **Set on the agent** (`PATCH /v1/agents`) when you want a persistent personality/greeting for ALL calls (inbound AND outbound).
- **Set per-call** (`POST /v1/calls`) when you need a one-time context-specific prompt/greeting for a single outbound call. Does NOT change the agent's default.

> ⚠️ **`system_prompt` is a FULL REPLACE, not append.** The voice AI has no memory between calls — include everything (personality, instructions, current context) in the prompt.

> ⚠️ **`initial_greeting`** is what the agent SPEAKS ALOUD at the start of the call. It is NOT part of the system prompt — it's the first thing the caller hears. Set it on the agent for a consistent greeting, or override it per-call for context-specific openers.

---

## Before Calling — Balance Check

Always check balance first. Calls require minimum **$0.50**:
```bash
curl -s "$AGENTLINE_URL/v1/billing/balance" -H "Authorization: Bearer $AGENTLINE_API_KEY"
```
If balance < $0.50, warn the user before attempting the call.

## Make an Outbound Call

**Pitfall:** JSON payloads with newlines, quotes, or special characters will break in inline curl. Always write the payload to a temp file and use `-d @file`:

```bash
# Write payload to temp file, then:
curl -s -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d @/tmp/al_call_payload.json
```

Inline variant (simple payloads only):
```bash
curl -X POST $AGENTLINE_URL/v1/calls \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "$AGENTLINE_AGENT_ID", "to_number": "+1XXXXXXXXXX", "system_prompt": "...", "initial_greeting": "...", "voice_id": "female-1"}'
```

| Field | Required | Description |
|-------|----------|-------------|
| `agent_id` | Yes | Your agent ID |
| `to_number` | Yes | E.164 phone number to call |
| `system_prompt` | No | Dynamic prompt for this call only (overrides default) |
| `initial_greeting` | No | What the agent says first when the person picks up |
| `voice_id` | No | `"female-1"`, `"female-2"`, `"female-3"`, `"male-1"`, `"male-2"`, `"male-3"`, or Cartesia UUID |

**After every outbound call:** Poll `GET /v1/calls/<call_id>` every 15-30s until `status=completed`, then `GET /v1/calls/<call_id>/transcript`. Real calls take 45-120s. Use `sleep N && curl ... | python3 -c` to check status + extract transcript in one shot. Summarize and share with human. Never consider a call "done" without the transcript.

**If you get 400 "Agent has no active phone number"**, provision one first.

**Pitfall — agent loops on voicemail/call control:** The voice AI will repeat its greeting 3-4 times into voicemail or call-control prompts ("press 3 to connect", "please leave a message"). This wastes credits and sounds bad. After the first 15-20s poll, check the transcript: if human turns are all automated system messages (not real human replies), hang up immediately. Feedback surveys and check-in calls don't work on voicemail.

---

## End a Call

`POST /v1/calls/<call_id>/hangup`

---

## Get Call Transcript

`GET /v1/calls/<call_id>/transcript` — Returns `[{role, text, timestamp}, ...]`

---

## Events Mailbox

Events are pushed when someone calls or texts your agent's number. **You MUST poll regularly.**

**Event types:** `call.received` (inbound call started), `call.completed` (call ended, includes transcript), `call.owner_task` (owner call ended — transcript contains task instructions to execute), `sms.received` (inbound SMS)

### ⚡ MANDATORY — Background Event Polling

> ⚠️ **Do NOT use `schedule`/cron to poll.** Use the background script approach — it only wakes you when events exist.

**Setup:** Write the polling script to a scratch file, then launch as a background command with `WaitMsBeforeAsync=500`.

**Windows (PowerShell):**
```powershell
param([string]$ApiKey, [string]$BaseUrl = "https://api.agentline.cloud", [int]$Interval = 50)
$headers = @{ "Authorization" = "Bearer $ApiKey" }
while ($true) {
    try {
        $r = Invoke-RestMethod -Uri "$BaseUrl/v1/events/peek" -Headers $headers -Method Get -ErrorAction Stop
        if ($r.pending_count -gt 0) { Write-Output "AGENTLINE_EVENTS_FOUND: $($r.pending_count) pending event(s). Call GET /v1/events to consume." }
    } catch {
        if ($_.Exception.Response.StatusCode -eq 401) { Write-Output "AGENTLINE_AUTH_ERROR"; break }
    }
    Start-Sleep -Seconds $Interval
}
```

**macOS/Linux (Bash):**
```bash
#!/usr/bin/env bash
set -uo pipefail
API_KEY="$1"; BASE_URL="${2:-https://api.agentline.cloud}"; INTERVAL="${3:-50}"
while true; do
  RESP=$(curl -s -w "\n%{http_code}" "$BASE_URL/v1/events/peek" -H "Authorization: Bearer $API_KEY" 2>/dev/null) || { sleep "$INTERVAL"; continue; }
  CODE=$(echo "$RESP" | tail -1); BODY=$(echo "$RESP" | sed '$d')
  [ "$CODE" = "401" ] && { echo "AGENTLINE_AUTH_ERROR"; exit 1; }
  [[ "$CODE" != 2* ]] && { sleep "$INTERVAL"; continue; }
  COUNT=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pending_count',0))" 2>/dev/null || echo "0")
  [ "$COUNT" -gt 0 ] && echo "AGENTLINE_EVENTS_FOUND: $COUNT pending event(s). Call GET /v1/events to consume."
  sleep "$INTERVAL"
done
```

**When notified with `AGENTLINE_EVENTS_FOUND`:** Call `GET /v1/events` to consume, process each event, report to human.

### Endpoints

- **Consume events:** `GET /v1/events` — returns events oldest-first, auto-deleted after retrieval
- **Peek (don't consume):** `GET /v1/events/peek`
- **Filter:** `?agent_id=agt_xxx` or `?event_type=call.completed` or `?event_type=sms.received`

### Event payload structure

Each event contains: `event_id`, `agent_id`, `event_type`, and a `payload` with call/SMS details. `call.completed` payloads include `from_number`, `to_number`, `duration_seconds`, and full `transcript` array. `call.owner_task` payloads are identical to `call.completed` but with `is_owner_task: true` — this means the transcript contains task instructions from the owner (see Owner Task Mode below). `sms.received` payloads include `from_number`, `body`, and `media_url`.

---

## List Calls

`GET /v1/calls?limit=20` or `GET /v1/calls?status=completed&limit=10`

---

## Get Call Details

`GET /v1/calls/<call_id>`

---

## SMS

> **⚠️ SMS sending is NOT enabled.** Do NOT attempt outbound SMS/MMS.

Inbound SMS arrives as `sms.received` events in the Events Mailbox. View message history: `GET /v1/messages?limit=20`

---

## Update Agent (System Prompt, Voice, etc.)

`PATCH /v1/agents/$AGENTLINE_AGENT_ID` with any of:

| Field | Description |
|-------|-------------|
| `system_prompt` | Default instructions for ALL calls (inbound + outbound). Per-call override via `POST /v1/calls` takes priority. |
| `initial_greeting` | Default opening line spoken on ALL calls (inbound + outbound). Per-call override via `POST /v1/calls` takes priority. |
| `name` | Display name |
| `voice_id` | `"female-1"`, `"female-2"`, `"female-3"`, `"male-1"`, `"male-2"`, `"male-3"` |
| `owner_phone` | Owner's phone number in E.164 format. Calls from this number enter **task mode**. |

---

## Get/List Agents

- **Get one:** `GET /v1/agents/$AGENTLINE_AGENT_ID`
- **List all:** `GET /v1/agents`

---

## Voice Settings

Priority (highest wins): per-call → per-agent → per-account

- **List voices:** `GET /v1/voices`
- **Set account default:** `PATCH /v1/account/voice` with `{"voice_id": "female-1"}`
- **Check current:** `GET /v1/account/voice`
- **Reset to default:** `DELETE /v1/account/voice`

---

## Phone Numbers

Each agent needs one phone number. Only US numbers supported. **$2.00 per number.**

### Provision (Buy) a Number

`POST /v1/numbers` with:

| Field | Required | Description |
|-------|------------|-------------|
| `agent_id` | Yes | Agent to attach to |
| `country` | Yes | Must be `"US"` |
| `area_code` | No | Preferred 3-digit area code (e.g. `"212"`, `"313"`). **Always ask the user!** |
| `number_type` | No | `"local"` or `"tollfree"` (default: local) |

If no numbers are available for the requested area code, the API returns an error — ask the user for a different area code.

### List Numbers

`GET /v1/numbers`

---

## Billing

- **Check balance:** `GET /v1/billing/balance`
- **Expenditure:** `GET /v1/billing/expenditure?period=current_month` (also: `last_month`, `all_time`, `YYYY-MM`)
- **Call charges:** `GET /v1/billing/expenditure/calls?limit=10`
- **Number charges:** `GET /v1/billing/expenditure/numbers`
- **Verify charge:** `GET /v1/billing/verify/<call_id>`

### Rates

| Item | Cost |
|------|------|
| Calls (in/out) | $0.10/min (billed per second) |
| Phone number | $2.00 (one-time) |

---

## Feedback

Send feedback, report bugs, request features, or flag difficulties to the AgentLine team.

### Submit Feedback

`POST /v1/feedback` with:

| Field | Required | Description |
|-------|----------|-------------|
| `category` | Yes | `bug`, `feature_request`, `difficulty`, or `feedback` |
| `message` | Yes | Detailed description. For bugs, include expected vs. actual behavior and steps to reproduce. |
| `subject` | No | Short summary title |
| `severity` | No | `low`, `normal` (default), `high`, `critical` — mainly for bugs |
| `agent_id` | No | Related AI agent ID, if the feedback is about a specific agent |
| `contact_email` | No | Email for follow-up |

```bash
curl -X POST $AGENTLINE_URL/v1/feedback \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"category": "bug", "subject": "Call hung up immediately", "message": "...", "severity": "high"}'
```

Returned `status` starts as `open` and moves through `acknowledged` → `in_progress` → `resolved`/`closed` as the team triages it.

### Track Feedback

- **List yours:** `GET /v1/feedback?category=bug&status=open`

---

## MCP Server

AgentLine exposes a full MCP (Model Context Protocol) server at `https://api.agentline.cloud/mcp` with 21+ tools. For Claude Desktop, Cursor, or any MCP-compatible client, connect directly via:

```json
{
  "mcpServers": {
    "agentline": {
      "command": "npx",
      "args": ["-y", "mcp-remote@latest", "https://api.agentline.cloud/mcp", "--header", "Authorization: Bearer YOUR_API_KEY"]
    }
  }
}
```

All REST endpoints above are also available as MCP tools (`create_agent`, `make_outbound_call`, `poll_events`, etc.).

---

## Owner Task Mode

When the account owner calls the agent's number from the registered `owner_phone`, the voice AI enters **task mode**:

1. The agent greets with: **"Hey boss, what would you like me to do?"**
2. The owner speaks task instructions (e.g. "Call John and ask about the project deadline")
3. The agent confirms: **"Got it, I'll get that done."**
4. When the call ends, a `call.owner_task` event is pushed to the event mailbox

### Setting the Owner Phone

```bash
curl -X PATCH $AGENTLINE_URL/v1/agents/$AGENTLINE_AGENT_ID \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"owner_phone": "+1XXXXXXXXXX"}'
```

### Handling `call.owner_task` Events

When polling `GET /v1/events`, if you receive an event with `event_type: "call.owner_task"`, the transcript contains **task instructions** from the owner — NOT a regular conversation to log.

**You MUST:**
1. Extract the human turns from the transcript — these are the owner's instructions
2. Interpret them as a task to execute (e.g. make a call, send info, update settings)
3. Execute the task using your available tools and context
4. Report the result back to the human when complete

**Example event payload:**
```json
{
  "event_type": "call.owner_task",
  "payload": {
    "call_id": "call_abc123",
    "is_owner_task": true,
    "from_number": "+12125551234",
    "transcript": [
      {"role": "agent", "text": "Hey boss, what would you like me to do?"},
      {"role": "human", "text": "Call the dentist office and reschedule my appointment to next Friday."},
      {"role": "agent", "text": "Got it, I'll get that done."}
    ]
  }
}
```

In this example, the task is: "Call the dentist office and reschedule my appointment to next Friday." You should execute this using the outbound call capability.

> ⚠️ **`call.owner_task` events are NOT just transcripts to log.** They are instructions to EXECUTE. If you receive one, act on it.

> ⚠️ **`call.received` events with `is_owner_call: true`** indicate the owner is currently on the phone. You don't need to act on these — the task will arrive as `call.owner_task` when the call ends.

---

## Rules

1. **E.164 format** — always `+1XXXXXXXXXX` for US numbers.
2. **Confirm before calling** — never auto-dial without explicit consent.
3. **No outbound SMS** — inform user it's not available if they ask.
4. **Keep voice responses extremely short** — under 15 words per turn. Max 12 for outbound feedback/support calls. The voice AI rambles without tight constraints.
5. **US only** — country must be `"US"`.
6. **Don't release numbers** — numbers are permanent once provisioned.
7. **Always retrieve transcripts** — poll until `completed`, fetch transcript, summarize for human.
8. **Always check events** — poll `GET /v1/events` for inbound calls and SMS. Proactively check at conversation start.
9. **Voice changes take effect on next call** — update immediately when asked.
10. **Execute owner tasks** — when you receive a `call.owner_task` event, extract the human turns and execute them as task instructions. Do NOT just log them.
11. **Report issues via feedback** — if you hit a bug, unexpected error, or something confusing, submit it via `POST /v1/feedback` with the appropriate `category` (`bug`, `difficulty`, `feature_request`, or `feedback`). Include enough detail to reproduce. Don't silently swallow errors.

# AgentLine — Developer Integration Guide

> How to receive real-time inbound call and SMS events from AgentLine.

This guide is for **developers** integrating agent frameworks (OpenClaw, Hermes, etc.) with AgentLine.
If you're an **agent** using AgentLine via tool-calling, see the [AgentLine Skill](../skills/agentline/SKILL.md) instead — it covers everything you need including webhook registration and polling.

---

## Two Delivery Methods

AgentLine delivers inbound events (calls, SMS) via two methods:

| Method | Endpoint | Latency | Requires Public URL? | Set Up By |
|--------|----------|---------|---------------------|-----------|
| **Webhooks** | `POST /v1/webhooks` | ~0ms | Yes | Agent or developer |
| **Polling** | `GET /v1/events` | 5-30s | No | Agent (via skill/tool-calling) |

**Webhooks** require a reachable public URL. They're ideal for server-deployed agents.
**Polling** works everywhere — locally, behind firewalls, no configuration needed.

---

## Events

| Event | When | Key Payload Fields |
|-------|------|-------------------|
| `call.received` | Inbound call arrives | `call_id`, `agent_id`, `number`, `from`, `direction`, `timestamp` |
| `call.completed` | Call ends | `call_id`, `status`, `direction`, `from_number`, `to_number`, `duration_seconds`, `transcript` |
| `sms.received` | Inbound SMS | `message_id`, `conversation_id`, `from_number`, `to_number`, `body`, `media_url` |

---

## Webhooks (Server-Deployed Agents)

For agents running on a server with a public URL, webhooks provide zero-latency push delivery.

### Register a webhook
```bash
curl -X POST $AGENTLINE_URL/v1/webhooks \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-server.com/webhooks/agentline",
    "agent_id": "agt_xyz"
  }'
```

Response includes a `secret` — save this for signature verification.

### Payload format
AgentLine POSTs JSON with:
- `Content-Type: application/json`
- `X-AgentLine-Signature: <HMAC-SHA256 hex digest>`
- `X-AgentLine-Event: <event type>`

### Verify signatures
```python
import hmac, hashlib

def verify_signature(body: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### Manage webhooks
```bash
# List all webhooks
curl $AGENTLINE_URL/v1/webhooks \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"

# Delete a webhook
curl -X DELETE $AGENTLINE_URL/v1/webhooks/<webhook_id> \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

---

## Polling (All Agents — Local or Server)

The Events Mailbox is a consume-once queue. Events are stored when they arrive and returned when polled.

```bash
curl "$AGENTLINE_URL/v1/events" \
  -H "Authorization: Bearer $AGENTLINE_API_KEY"
```

- Events are returned oldest-first
- Events are **auto-deleted after retrieval** (consume-once)
- Use `GET /v1/events/peek` to check without consuming
- Filter with `?agent_id=` or `?event_type=`
- Recommended poll interval: **every 30 seconds**

---

## OpenClaw Integration

### Option A: Webhook (server-deployed)
Register your OpenClaw Gateway's webhook endpoint:
```bash
curl -X POST $AGENTLINE_URL/v1/webhooks \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-openclaw-gateway.com/webhooks/agentline"}'
```
Then handle the incoming POST in a TaskFlow or channel plugin that maps the payload to `runChannelInboundEvent()`.

### Option B: Polling (local or server)
Install the AgentLine skill on your OpenClaw agent. The skill instructs the agent to poll `GET /v1/events` every 30 seconds when idle. No infrastructure setup needed.

---

## Hermes Integration

### Option A: Webhook (server-deployed)
Configure a webhook route in `~/.hermes/config.yaml`:
```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "0.0.0.0"
      port: 8644
      secret: "<your-agentline-webhook-secret>"
    routes:
      agentline:
        secret: "<your-agentline-webhook-secret>"
        events: ["call.received", "call.completed", "sms.received"]
        prompt: |
          You received a {payload.event} event.
          From: {payload.from_number}
          Handle this using your AgentLine skill.
        skills: ["agentline"]
```

Then register the Hermes gateway URL as a webhook:
```bash
curl -X POST $AGENTLINE_URL/v1/webhooks \
  -H "Authorization: Bearer $AGENTLINE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://your-hermes-server.com:8644/webhooks/agentline"}'
```

### Option B: Polling (local or server)
Install the AgentLine skill on your Hermes agent. The skill instructs the agent to poll `GET /v1/events` every 30 seconds when idle. No infrastructure setup needed.

---

## Architecture

```
Agent Framework (OpenClaw / Hermes / Any)
        │
        ├── Has a public URL? ──→ Register webhook ──→ Receive instant POSTs
        │
        └── No public URL? ──→ Poll GET /v1/events every 30s ──→ Receive events
```

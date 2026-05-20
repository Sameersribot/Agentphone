# AgentLine

AI-native telephony platform — give your agent a phone number, voice, and SMS.

## Stack

- **FastAPI** — async Python API server
- **PostgreSQL** (Supabase) — persistent storage
- **Redis** — caching & rate limiting
- **Plivo & SignalWire** — phone numbers, SMS, voice calls (Multi-Provider)
- **Deepgram** — pre-recorded and real-time speech-to-text

## Quick Start

### 1. Clone and configure

```bash
cp .env.example .env
# Fill in your API keys in .env
# You will need credentials from both Plivo (for IN numbers) and SignalWire (for US numbers)
```

### 2. Run with Docker Compose

```bash
docker-compose up -d
```

This starts:
- **API server** at `http://localhost:8000`
- **PostgreSQL** at `localhost:5432` (schema auto-applied)
- **Redis** at `localhost:6379`

### 3. Or run locally

```bash
pip install -r requirements.txt
uvicorn agentline.main:app --reload
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET/POST` | `/v1/agents` | CRUD agents |
| `GET/POST` | `/v1/numbers` | Provision/release phone numbers |
| `GET/POST` | `/v1/messages` | Send/list SMS messages |
| `GET/POST` | `/v1/calls` | Initiate/list voice calls |
| `POST` | `/v1/calls/{id}/speak` | Send TTS response on active call |
| `POST` | `/v1/calls/{id}/hangup` | Terminate an active call |
| `GET` | `/v1/calls/{id}/listen` | Poll for caller speech (long-poll) |
| `GET/POST` | `/v1/webhooks` | Configure event webhooks |
| `GET` | `/v1/usage` | Usage statistics |

## Number Provisioning

AgentLine uses a **multi-provider strategy** to ensure maximum reliability and cost-effectiveness:
- **US Numbers (`country: "US"`)**: Automatically provisioned and routed via **SignalWire**.
- **Indian Numbers (`country: "IN"`)**: Automatically provisioned and routed via **Plivo**.

When calling `POST /v1/numbers`, simply specify the `country` ("US" or "IN"), and the backend will handle routing the purchase to the correct provider. All downstream voice and SMS actions for that number will seamlessly use the provider it was purchased from.

## Voice Pipeline (Hybrid Relay Mode)

Instead of a fragile real-time websocket, the system uses an asynchronous Hybrid Relay architecture:

```
Provider (Plivo/SignalWire) answers call
    → Plays TTS greeting
    → Records caller's speech (<Record>)
    → Deepgram STT (Pre-recorded, fast & accurate)
    → Webhook dispatched to your Agent
    → Provider enters silent <Wait> loop
    → Agent responds via `POST /v1/calls/{id}/speak`
    → Provider plays agent's response and loops back to recording
    → Agent can end the call at any time via `POST /v1/calls/{id}/hangup`
```

## Telephony Providers

This project currently supports **Plivo** and **SignalWire**. A full Telnyx revert snapshot is preserved in `TELNYX_REVERT_SNAPSHOT.md` if you ever need to switch back to Telnyx.

## API Docs

Once running, visit `http://localhost:8000/docs` for the interactive Swagger UI.

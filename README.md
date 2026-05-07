# AgentLine

AI-native telephony platform — give your agent a phone number, voice, and SMS.

## Stack

- **FastAPI** — async Python API server
- **PostgreSQL** (Supabase) — persistent storage
- **Redis** — caching & rate limiting
- **Plivo** — phone numbers, SMS, voice calls
- **Deepgram** — real-time speech-to-text
- **Cartesia** — text-to-speech
- **Claude / GPT-4o** — conversational AI

## Quick Start

### 1. Clone and configure

```bash
cp .env.example .env
# Fill in your API keys in .env
# Sign up at https://console.plivo.com for Plivo credentials
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
| `POST` | `/v0/agent/signup` | Send OTP to human's email |
| `POST` | `/v0/agent/verify` | Verify OTP → get account + API key |
| `GET/POST` | `/v1/agents` | CRUD agents |
| `GET/POST` | `/v1/numbers` | Provision/release phone numbers |
| `GET/POST` | `/v1/messages` | Send/list SMS messages |
| `GET/POST` | `/v1/calls` | Initiate/list voice calls |
| `GET/POST` | `/v1/webhooks` | Configure event webhooks |
| `GET` | `/v1/usage` | Usage statistics |

## Voice Pipeline

```
Plivo WS (mulaw audio in)
    → Deepgram STT (streaming)
    → Claude / GPT-4o (response)
    → Cartesia TTS (mulaw audio out)
    → Plivo WS (back to caller)
```

## Telephony Provider

This project uses **Plivo** for telephony. A full Telnyx revert snapshot is preserved in
`TELNYX_REVERT_SNAPSHOT.md` if you ever need to switch back.

## API Docs

Once running, visit `http://localhost:8000/docs` for the interactive Swagger UI.

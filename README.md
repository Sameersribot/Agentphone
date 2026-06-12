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

---

## MCP Server Integration

AgentLine exposes all its REST endpoints as **MCP (Model Context Protocol) tools**, letting AI agents (Claude Desktop, Cursor, custom agents) call telephony functions directly — no curl or HTTP wrappers needed.

### Quick Start

1. **Start AgentLine** (locally or deployed):
   ```bash
   uvicorn agentline.main:app --port 8000
   ```

2. **Connect from Claude Desktop** — copy `claude_desktop_config_example.json` to your Claude Desktop config folder:
   
   **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`  
   **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

   ```json
   {
     "mcpServers": {
       "agentline": {
         "command": "npx",
         "args": [
           "-y", "mcp-remote@latest",
           "http://localhost:8000/mcp",
           "--header", "Authorization: Bearer sk_live_YOUR_KEY"
         ]
       }
     }
   }
   ```

3. **For production** (deployed on Railway/Render):
   ```json
   {
     "mcpServers": {
       "agentline": {
         "command": "npx",
         "args": [
           "-y", "mcp-remote@latest",
           "https://api.agentline.cloud/mcp",
           "--header", "Authorization: Bearer sk_live_YOUR_KEY"
         ]
       }
     }
   }
   ```

| Category | Tool | Description |
|----------|------|-------------|
| **Agents** | `create_agent` | Create a new AI voice agent |
| | `list_agents` | List all agents |
| | `get_agent` | Get agent details |
| | `update_agent` | Update agent configuration (prompt, greeting, voice) |
| | `delete_agent` | Delete an agent |
| **Phone Numbers** | `buy_phone_number` | Search and provision a new local US phone number |
| | `list_phone_numbers` | List all phone numbers provisioned on the account |
| **Calls** | `make_outbound_call` | Initiate an outbound call and run hosted voice conversation |
| | `list_calls` | List voice call history with filter status |
| | `get_call_details` | Get details and metadata of a specific call |
| | `get_call_transcript` | Retrieve the conversation transcript for a call |
| | `hangup_call` | Forcefully end an ongoing voice call |
| **SMS** | `list_messages` | List inbound message history (inbound only) |
| **Events** | `poll_events` | Poll events from the consume-once mailbox |
| | `peek_events` | Peek at mailbox events without consuming them |
| **Billing** | `get_account_balance` | Check current prepaid account balance |
| | `get_expenditure_breakdown` | Get spending breakdown split by category |
| **Voice** | `list_available_voices` | List all available voice presets |
| | `get_account_voice` | Get current account-wide default voice |
| | `set_account_voice` | Set default voice for all agents under this account |
| | `reset_account_voice` | Reset account default voice to system default (`male-1`) |

### Using with Other MCP Clients

Any MCP-compatible client can connect to `http://your-server:8000/mcp` using the SSE transport. Pass your API key via the `Authorization` header.

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

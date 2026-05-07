# Telnyx Revert Snapshot

> **Purpose:** This file preserves the complete Telnyx implementation so the project can be
> reverted from Plivo back to Telnyx at any time. Each section contains the **exact file
> contents** that need to be restored, along with the file path.

---

## Migration Summary

| Area | Telnyx | Plivo |
|------|--------|-------|
| SDK | `telnyx>=2.0.0` (async native) | `plivo>=4.0.0` (sync, wrapped in `run_in_executor`) |
| Env Vars | `TELNYX_API_KEY`, `TELNYX_PUBLIC_KEY`, `TELNYX_CONNECTION_ID`, `TELNYX_MESSAGING_PROFILE_ID` | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_APP_ID` |
| Number Provisioning | `client.available_phone_numbers.list()` → `client.number_orders.create()` | `client.numbers.search()` → `client.numbers.buy()` |
| Number Release | `client.phone_numbers.delete(telnyx_id)` | `client.numbers.delete(number=phone_number)` |
| SMS | `client.messages.send()` with `messaging_profile_id` | `client.messages.create()` with `src`/`dst`/`text` |
| Outbound Call | `client.calls.dial()` with `connection_id` + `client_state` (base64) | `client.calls.create()` with `answer_url` |
| Voice Streaming | Telnyx Media Fork (WebSocket `start_streaming`) | Plivo `<Stream bidirectional>` XML |
| Inbound SMS Webhook | JSON body → `data.payload.from.phone_number` | Form-encoded → `From`, `To`, `Text` |
| Inbound Voice Webhook | JSON body → `data.event_type`, `data.payload` | Form-encoded → Plivo XML response |
| DB Column | `telnyx_id` in `phone_numbers`, `telnyx_call_id` in `calls`, `telnyx_message_id` in `messages` | `provider_id` (generic) |
| Router Module | `routers/telnyx_events.py` | `routers/plivo_events.py` |

---

## Files to Restore for Telnyx Revert

### 1. `requirements.txt` — Replace `plivo>=4.0.0` with `telnyx>=2.0.0`

```txt
# AgentLine Backend Dependencies
fastapi>=0.111.0
uvicorn[standard]>=0.30.1
asyncpg>=0.30.0
telnyx>=2.0.0
deepgram-sdk>=3.2.7
openai>=1.30.0
httpx>=0.27.0
python-jose[cryptography]>=3.3.0
bcrypt>=4.0.1
python-multipart>=0.0.9
pydantic-settings>=2.3.1
email-validator>=2.0.0
websockets>=12.0
```

### 2. `.env` — Replace Plivo vars with Telnyx vars

```env
# Telnyx — REDACTED for GitHub push protection
# Retrieve your actual keys from your Telnyx dashboard or local .env backup
TELNYX_API_KEY=<your-telnyx-api-key>
TELNYX_PUBLIC_KEY=<your-telnyx-public-key>
TELNYX_CONNECTION_ID=<your-telnyx-connection-id>
TELNYX_MESSAGING_PROFILE_ID=<your-telnyx-messaging-profile-id>
```

### 3. `.env.example` — Replace Plivo section with Telnyx section

```env
# Telnyx
TELNYX_API_KEY=KEY_xxx
TELNYX_PUBLIC_KEY=xxx
TELNYX_CONNECTION_ID=
TELNYX_MESSAGING_PROFILE_ID=
```

### 4. `agentline/config.py` — Restore Telnyx settings

Replace the Plivo settings block:
```python
    # Telnyx
    TELNYX_API_KEY: str = ""
    TELNYX_PUBLIC_KEY: str = ""
    TELNYX_CONNECTION_ID: str = ""
    TELNYX_MESSAGING_PROFILE_ID: str = ""
```

### 5. `agentline/telnyx_client.py` — Restore entire file (was replaced by `agentline/plivo_client.py`)

```python
"""
AgentLine — Telnyx Client
Wraps Telnyx SDK for number provisioning, SMS, and call initiation.
"""

import telnyx
from agentline.config import settings

client = telnyx.AsyncTelnyx(api_key=settings.TELNYX_API_KEY)


async def provision_number(
    country: str = "US",
    area_code: str | None = None,
    agent_id: str | None = None,
) -> dict:
    """
    Search for and purchase a phone number from Telnyx.
    Returns dict with phone_number (E.164) and telnyx_id.
    """
    params = {
        "country_code": country,
        "features": ["sms", "voice"],
    }
    if area_code:
        params["national_destination_code"] = area_code

    numbers = await client.available_phone_numbers.list(filter=params)
    if not numbers.data:
        raise Exception(f"No numbers available in {country} {area_code or ''}")

    chosen = numbers.data[0].phone_number

    # Purchase the number and bind it to our TeXML application
    order = await client.number_orders.create(
        phone_numbers=[{"phone_number": chosen}],
        connection_id=settings.TELNYX_CONNECTION_ID,
    )

    return {
        "phone_number": chosen,
        "telnyx_id": order.data.phone_numbers[0].id if order.data and order.data.phone_numbers else "pending",
    }


async def release_number(telnyx_id: str):
    """Release a phone number back to Telnyx."""
    await client.phone_numbers.delete(telnyx_id)


async def send_sms(
    from_number: str,
    to_number: str,
    body: str,
    media_url: str | None = None,
) -> dict:
    """Send an SMS/MMS via Telnyx."""
    params = {
        "from_": from_number,
        "to": to_number,
        "text": body,
        "messaging_profile_id": settings.TELNYX_MESSAGING_PROFILE_ID,
    }
    if media_url:
        params["media_urls"] = [media_url]

    result = await client.messages.send(**params)
    
    # Safely extract status from Pydantic model response
    status = "queued"
    if result.data and result.data.to:
        to_item = result.data.to[0]
        if hasattr(to_item, "status"):
            status = to_item.status
        elif isinstance(to_item, dict):
            status = to_item.get("status", "queued")

    return {
        "telnyx_message_id": result.data.id if result.data else "unknown",
        "status": status,
    }


import base64

async def initiate_call(
    from_number: str,
    to_number: str,
    call_id: str,
) -> str:
    """
    Place an outbound call via Telnyx.
    call_id is our internal ID, passed as client_state for webhook correlation.
    Returns the Telnyx call_control_id.
    """
    encoded_state = base64.b64encode(call_id.encode('utf-8')).decode('utf-8')
    result = await client.calls.dial(
        connection_id=settings.TELNYX_CONNECTION_ID,
        from_=from_number,
        to=to_number,
        client_state=encoded_state,
        webhook_url=f"{settings.BASE_URL}/telnyx/voice",
    )
    return result.data.call_control_id if result.data else "unknown"
```

### 6. `agentline/routers/telnyx_events.py` — Restore entire file (was replaced by `agentline/routers/plivo_events.py`)

```python
"""
AgentLine — Telnyx Events Router
Handles Telnyx voice webhooks and media WebSocket connections.
"""

import secrets
import json
import logging
import base64

import telnyx
from fastapi import APIRouter, Request, WebSocket

from agentline.config import settings
from agentline.database import get_db_conn
from agentline.webhook_dispatcher import dispatch_webhook
from agentline.telnyx_client import client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telnyx", tags=["Telnyx Events"])


@router.post("/voice")
async def telnyx_voice_webhook(request: Request):
    """Receive Telnyx call control events."""
    body = await request.json()
    event_type = body["data"]["event_type"]
    payload = body["data"]["payload"]

    call_control_id = payload.get("call_control_id")
    client_state_raw = payload.get("client_state")
    
    # client_state is passed as base64 encoded string from our initiate_call in Telnyx SDK v4
    try:
        call_id = base64.b64decode(client_state_raw).decode('utf-8') if client_state_raw else None
    except Exception:
        call_id = client_state_raw

    if event_type == "call.initiated":
        logger.info("Call %s initiated", call_id)

    elif event_type == "call.answered":
        # Call connected — start media streaming to our WebSocket
        logger.info("Call %s answered, starting media stream", call_id)
        # Strip protocol and trailing slashes for WS URL
        host = settings.BASE_URL.replace("https://", "").replace("http://", "").rstrip("/")
        await client.calls.actions.start_streaming(
            call_control_id=call_control_id,
            stream_url=f"wss://{host}/telnyx/media/{call_id}",
            stream_track="inbound_track",
        )

    elif event_type == "call.hangup":
        logger.info("Call %s hung up", call_id)
        if call_id:
            async with get_db_conn() as db:
                await db.execute(
                    "UPDATE calls SET status='completed', ended_at=now() WHERE id=$1 AND status!='completed'",
                    call_id,
                )

    elif event_type == "call.streaming.started":
        logger.info("Media streaming started for call %s", call_id)

    elif event_type == "call.streaming.stopped":
        logger.info("Media streaming stopped for call %s", call_id)

    return {"status": "ok"}


@router.websocket("/media/{call_id}")
async def telnyx_media_ws(websocket: WebSocket, call_id: str):
    """
    WebSocket endpoint that Telnyx streams raw audio to.
    Runs the full voice pipeline: STT → LLM → TTS.
    """
    await websocket.accept()
    logger.info("Media WebSocket connected for call %s", call_id)

    try:
        # Fetch call + agent config
        async with get_db_conn() as db:
            call = await db.fetchrow("SELECT * FROM calls WHERE id=$1", call_id)
            if not call:
                logger.error("Call %s not found in DB", call_id)
                await websocket.close()
                return

            agent = await db.fetchrow("SELECT * FROM agents WHERE id=$1", call["agent_id"])

        # Import here to avoid circular imports
        from agentline.voice.pipeline import run_pipeline

        await run_pipeline(
            telnyx_ws=websocket,
            call_id=call_id,
            system_prompt=call["system_prompt"] or (agent["system_prompt"] if agent else ""),
            initial_greeting=agent["initial_greeting"] if agent else None,
            voice_id=agent["voice_id"] if agent else "cartesia-sonic-english",
            model_tier=agent["model_tier"] if agent else "balanced",
        )
    except Exception as e:
        logger.exception("Voice pipeline error for call %s: %s", call_id, e)
    finally:
        logger.info("Media WebSocket closed for call %s", call_id)


@router.post("/sms")
async def telnyx_sms_webhook(request: Request):
    """Receive inbound SMS from Telnyx and dispatch to customer webhook."""
    body = await request.json()
    payload = body["data"]["payload"]

    direction = payload.get("direction", "inbound")
    if direction != "inbound":
        return {"status": "skipped"}

    from_number = payload["from"]["phone_number"]
    to_number = payload["to"][0]["phone_number"]
    text = payload.get("text", "")

    async with get_db_conn() as db:
        number = await db.fetchrow(
            "SELECT * FROM phone_numbers WHERE phone_number=$1", to_number,
        )
        if not number:
            return {"status": "unknown_number"}

        # Upsert conversation
        conv = await db.fetchrow(
            "SELECT * FROM conversations WHERE number_id=$1 AND contact_number=$2",
            number["id"], from_number,
        )
        if not conv:
            conv_id = f"conv_{secrets.token_urlsafe(12)}"
            await db.execute(
                """INSERT INTO conversations (id, account_id, agent_id, number_id, contact_number)
                   VALUES ($1,$2,$3,$4,$5)""",
                conv_id, number["account_id"], number["agent_id"],
                number["id"], from_number,
            )
        else:
            conv_id = conv["id"]

        # Save inbound message
        msg_id = f"msg_{secrets.token_urlsafe(12)}"
        await db.execute(
            """INSERT INTO messages
               (id, account_id, agent_id, number_id, conversation_id,
                direction, from_number, to_number, body)
               VALUES ($1,$2,$3,$4,$5,'inbound',$6,$7,$8)""",
            msg_id, number["account_id"], number["agent_id"],
            number["id"], conv_id, from_number, to_number, text,
        )

    # Fire webhook to customer
    await dispatch_webhook(number["account_id"], number["agent_id"], {
        "event": "agent.message",
        "channel": "sms",
        "agent_id": number["agent_id"],
        "number_id": number["id"],
        "from_number": from_number,
        "to_number": to_number,
        "content": text,
        "conversation_id": conv_id,
    })

    return {"status": "ok"}
```

### 7. `agentline/main.py` — Restore Telnyx router import

Replace:
```python
from agentline.routers import auth, agents, numbers, messages, calls, webhooks, usage, plivo_events
```
With:
```python
from agentline.routers import auth, agents, numbers, messages, calls, webhooks, usage, telnyx_events
```

Replace:
```python
app.include_router(plivo_events.router)
```
With:
```python
app.include_router(telnyx_events.router)
```

### 8. `agentline/routers/numbers.py` — Restore Telnyx imports and column names

Replace:
```python
from agentline.plivo_client import provision_number, release_number
```
With:
```python
from agentline.telnyx_client import provision_number, release_number
```

Restore `telnyx_id` column references in the INSERT and `release_number(row["telnyx_id"])`.

### 9. `agentline/routers/calls.py` — Restore Telnyx imports and column names

Replace:
```python
from agentline.plivo_client import initiate_call
```
With:
```python
from agentline.telnyx_client import initiate_call
```

Restore `telnyx_call_id` column in UPDATE statement.

### 10. `agentline/routers/messages.py` — Restore Telnyx imports and column names

Replace:
```python
from agentline.plivo_client import send_sms
```
With:
```python
from agentline.telnyx_client import send_sms
```

Restore `telnyx_message_id` column references.

### 11. `agentline/voice/pipeline.py` — Restore Telnyx references in comments and `telnyx_ws` parameter names

Restore the docstring to reference Telnyx, and the Plivo-specific WebSocket `playAudio` event back to Telnyx's `media` event format.

### 12. `agentline/voice/tts.py` — Restore Telnyx comment in docstring

Restore `Returns raw audio bytes ready for Telnyx media stream.`

### 13. `schema.sql` — Restore `telnyx_id`, `telnyx_call_id`, `telnyx_message_id` column names

Replace `provider_id` → `telnyx_id`, `provider_call_id` → `telnyx_call_id`, `provider_message_id` → `telnyx_message_id`.

### 14. `force_provision.py` and `verify_magiclink.py` — Restore Telnyx imports

Replace `from agentline.plivo_client import provision_number` with `from agentline.telnyx_client import provision_number`.
Restore `telnyx_id` column references.

### 15. `test_telnyx.py` — Restore as-is (currently at `test_plivo.py`)

Rename `test_plivo.py` back to `test_telnyx.py` and restore contents.

---

## Database Migration for Revert

Run these SQL commands to rename columns back:

```sql
-- Revert phone_numbers
ALTER TABLE phone_numbers RENAME COLUMN provider_id TO telnyx_id;

-- Revert calls
ALTER TABLE calls RENAME COLUMN provider_call_id TO telnyx_call_id;

-- Revert messages
ALTER TABLE messages RENAME COLUMN provider_message_id TO telnyx_message_id;
```

---

## Revert Checklist

1. [ ] Restore `.env` Telnyx keys (from section 2 above)
2. [ ] Restore `.env.example` (from section 3)
3. [ ] Replace `plivo>=4.0.0` with `telnyx>=2.0.0` in `requirements.txt`
4. [ ] Restore `agentline/config.py` Telnyx settings
5. [ ] Delete `agentline/plivo_client.py`, restore `agentline/telnyx_client.py`
6. [ ] Delete `agentline/routers/plivo_events.py`, restore `agentline/routers/telnyx_events.py`
7. [ ] Update `agentline/main.py` imports
8. [ ] Update `agentline/routers/numbers.py` imports + column names
9. [ ] Update `agentline/routers/calls.py` imports + column names
10. [ ] Update `agentline/routers/messages.py` imports + column names
11. [ ] Update `agentline/voice/pipeline.py` comments + WebSocket format
12. [ ] Update `agentline/voice/tts.py` docstring
13. [ ] Run database migration SQL (see above)
14. [ ] Restore `force_provision.py` and `verify_magiclink.py` imports
15. [ ] Rename `test_plivo.py` → `test_telnyx.py`
16. [ ] Update `README.md` and `skills/agentline/SKILL.md`
17. [ ] `pip install -r requirements.txt`
18. [ ] Test all endpoints

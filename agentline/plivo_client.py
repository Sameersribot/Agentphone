"""
AgentLine — Plivo Client
Wraps Plivo Python SDK v4+ for number provisioning, SMS, and voice calls.

Plivo SDK is synchronous — all methods use run_in_executor for async FastAPI.

API Reference:
  Numbers: https://www.plivo.com/docs/numbers/api/phone-number
  Calls:   https://www.plivo.com/docs/voice/api/calls
  SMS:     https://www.plivo.com/docs/messaging/api/message
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import plivo
from agentline.config import settings

logger = logging.getLogger(__name__)

# Lazy client + thread pool
_client = None
_executor = ThreadPoolExecutor(max_workers=4)


def _get_client():
    """Lazy-init the Plivo RestClient (avoids import-time env errors)."""
    global _client
    if _client is None:
        if not settings.PLIVO_AUTH_ID or not settings.PLIVO_AUTH_TOKEN:
            raise RuntimeError("PLIVO_AUTH_ID and PLIVO_AUTH_TOKEN must be set.")
        _client = plivo.RestClient(settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN)
    return _client


async def _run(func, *args, **kwargs):
    """Run a blocking Plivo SDK call on the thread-pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


# ────────────────────────────────────────────────────────────
# Number Provisioning
# Plivo docs: GET /v1/Account/{auth_id}/PhoneNumber/ → search
#              POST /v1/Account/{auth_id}/PhoneNumber/{number}/ → buy
# ────────────────────────────────────────────────────────────

async def provision_number(
    country: str = "IN",
    number_type: str = "local",
    pattern: str | None = None,
    agent_id: str | None = None,
) -> dict:
    """
    Search for and buy a phone number from Plivo's inventory.

    Args:
        country:     ISO-2 country code (default 'IN' for India)
        number_type: 'local', 'mobile', 'tollfree', 'fixed', 'national'
        pattern:     Area code or prefix filter (e.g. '22' for Mumbai)
        agent_id:    Our internal agent_id (unused by Plivo, for logging)

    Returns: {"phone_number": "+91...", "provider_id": "91..."}
    """
    # Step 1: Search available numbers
    search_kwargs = {
        "country_iso": country,
        "type": number_type,
        "limit": 5,
    }
    if pattern:
        search_kwargs["pattern"] = pattern

    logger.info("Searching Plivo numbers: %s", search_kwargs)

    try:
        response = await _run(_get_client().numbers.search, **search_kwargs)
    except plivo.exceptions.PlivoRestError as e:
        raise Exception(f"Plivo number search failed: {e}")

    # response is a list-like of PhoneNumber objects
    # Convert to list if needed
    numbers_list = list(response) if response else []

    if not numbers_list:
        raise Exception(
            f"No {number_type} numbers available in {country}. "
            f"Check Plivo console for available inventory or KYC compliance."
        )

    # Pick the first available number
    chosen = numbers_list[0]
    number_str = chosen.number if hasattr(chosen, "number") else str(chosen)

    logger.info("Found number: %s — buying...", number_str)

    # Step 2: Buy the number
    # Plivo API: POST /v1/Account/{auth_id}/PhoneNumber/{number}/
    try:
        await _run(_get_client().numbers.buy, number=number_str)
    except plivo.exceptions.PlivoRestError as e:
        raise Exception(f"Failed to buy number {number_str}: {e}")

    # Format as E.164
    phone_e164 = f"+{number_str}" if not number_str.startswith("+") else number_str

    logger.info("Successfully provisioned: %s", phone_e164)

    return {
        "phone_number": phone_e164,
        "provider_id": number_str,  # Plivo uses the raw number as ID
    }


async def release_number(provider_id: str):
    """
    Unrent a number from your Plivo account.
    Plivo API: DELETE /v1/Account/{auth_id}/Number/{number}/
    """
    number = provider_id.lstrip("+")
    try:
        await _run(_get_client().numbers.delete, number=number)
        logger.info("Released number: %s", number)
    except plivo.exceptions.PlivoRestError as e:
        logger.warning("Failed to release number %s: %s", number, e)


async def list_plivo_numbers() -> list[dict]:
    """
    List all numbers currently rented on this Plivo account.
    Useful for debugging or manual attachment.
    """
    try:
        response = await _run(_get_client().numbers.list, limit=20)
        numbers = list(response) if response else []
        return [
            {
                "number": n.number if hasattr(n, "number") else str(n),
                "type": getattr(n, "number_type", "unknown"),
                "voice_enabled": getattr(n, "voice_enabled", False),
                "sms_enabled": getattr(n, "sms_enabled", False),
            }
            for n in numbers
        ]
    except Exception as e:
        logger.error("Failed to list Plivo numbers: %s", e)
        return []


# ────────────────────────────────────────────────────────────
# SMS
# Plivo API: POST /v1/Account/{auth_id}/Message/
# Required: src, dst, text
# ────────────────────────────────────────────────────────────

async def send_sms(
    from_number: str,
    to_number: str,
    body: str,
    media_url: str | None = None,
) -> dict:
    """
    Send an SMS or MMS message via Plivo.

    Args:
        from_number: Plivo-rented number (E.164)
        to_number:   Destination number (E.164)
        body:        Message text
        media_url:   Optional media URL for MMS

    Returns: {"provider_message_id": "uuid", "status": "queued"}
    """
    params = {
        "src": from_number.lstrip("+"),
        "dst": to_number.lstrip("+"),
        "text": body,
    }
    if media_url:
        params["media_urls"] = [media_url]
        params["type"] = "mms"

    try:
        response = await _run(_get_client().messages.create, **params)
    except plivo.exceptions.PlivoRestError as e:
        raise Exception(f"Plivo SMS failed: {e}")

    # Response has message_uuid (list) and api_id
    message_uuid = ""
    if hasattr(response, "message_uuid"):
        uuids = response.message_uuid
        message_uuid = uuids[0] if isinstance(uuids, list) else str(uuids)
    elif isinstance(response, tuple) and len(response) > 1:
        resp = response[1]
        if hasattr(resp, "message_uuid"):
            uuids = resp.message_uuid
            message_uuid = uuids[0] if isinstance(uuids, list) else str(uuids)

    return {
        "provider_message_id": message_uuid or "unknown",
        "status": "queued",
    }


# ────────────────────────────────────────────────────────────
# Outbound Calls
# Plivo API: POST /v1/Account/{auth_id}/Call/
# Required: from_, to_, answer_url, answer_method
# ────────────────────────────────────────────────────────────

async def initiate_call(
    from_number: str,
    to_number: str,
    call_id: str,
) -> str:
    """
    Place an outbound voice call via Plivo.

    When the callee answers, Plivo will POST to our answer_url which returns
    XML with a <Stream bidirectional> element to start the voice pipeline.

    Args:
        from_number: Plivo-rented number (E.164)
        to_number:   Destination number (E.164)
        call_id:     Our internal call ID — embedded in the answer_url path

    Returns: Plivo request_uuid (call identifier)
    """
    answer_url = f"{settings.base_url_clean}/plivo/answer/{call_id}"
    hangup_url = f"{settings.base_url_clean}/plivo/hangup/{call_id}"

    try:
        response = await _run(
            _get_client().calls.create,
            from_=from_number.lstrip("+"),
            to_=to_number.lstrip("+"),
            answer_url=answer_url,
            answer_method="POST",
            hangup_url=hangup_url,
            hangup_method="POST",
        )
    except plivo.exceptions.PlivoRestError as e:
        raise Exception(f"Plivo call failed: {e}")

    # Response has request_uuid
    request_uuid = ""
    if hasattr(response, "request_uuid"):
        request_uuid = response.request_uuid
    elif isinstance(response, tuple) and len(response) > 1:
        resp = response[1]
        request_uuid = getattr(resp, "request_uuid", "") or ""

    logger.info("Outbound call initiated: %s → %s (uuid: %s)", from_number, to_number, request_uuid)
    return request_uuid or "unknown"


async def hangup_call(provider_call_id: str) -> None:
    """
    Terminate a live call via Plivo.

    Plivo API: DELETE /v1/Account/{auth_id}/Call/{call_uuid}/

    Args:
        provider_call_id: The Plivo call UUID (request_uuid returned at initiation)
    """
    try:
        await _run(_get_client().calls.hangup, call_uuid=provider_call_id)
        logger.info("Plivo hangup successful: %s", provider_call_id)
    except plivo.exceptions.PlivoRestError as e:
        logger.warning("Plivo hangup failed for %s: %s", provider_call_id, e)
        raise Exception(f"Plivo hangup failed: {e}")

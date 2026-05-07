"""
AgentLine — Plivo Client
Wraps Plivo SDK for number provisioning, SMS, and call initiation.
Plivo's SDK is synchronous — we use run_in_executor for async compatibility.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import plivo
from agentline.config import settings

logger = logging.getLogger(__name__)

# Plivo SDK is synchronous — share a single RestClient and executor
client = plivo.RestClient(settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN)
_executor = ThreadPoolExecutor(max_workers=4)


def _run_sync(func, *args, **kwargs):
    """Helper: run a blocking Plivo SDK call on the thread-pool executor."""
    loop = asyncio.get_event_loop()
    return loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


# ────────────────────────────────────────────────────────────
# Number Provisioning
# ────────────────────────────────────────────────────────────

async def provision_number(
    country: str = "US",
    area_code: str | None = None,
    agent_id: str | None = None,
) -> dict:
    """
    Search for and purchase a phone number from Plivo.
    Returns dict with phone_number (E.164) and provider_id.
    """
    search_params = {
        "country_iso": country,
        "type": "local",
        "services": "voice,sms",
        "limit": 1,
    }
    if area_code:
        search_params["pattern"] = area_code

    results = await _run_sync(client.numbers.search, **search_params)

    # results is a tuple (status_code, response_object)
    numbers_list = results[1] if isinstance(results, tuple) else results
    if hasattr(numbers_list, "objects"):
        numbers_list = numbers_list.objects
    elif isinstance(numbers_list, dict):
        numbers_list = numbers_list.get("objects", [])

    if not numbers_list:
        raise Exception(f"No numbers available in {country} {area_code or ''}")

    chosen_obj = numbers_list[0]
    chosen_number = chosen_obj.number if hasattr(chosen_obj, "number") else chosen_obj.get("number", "")

    # Buy the number and attach to our Plivo application
    buy_params = {"number": chosen_number}

    await _run_sync(client.numbers.buy, **buy_params)

    return {
        "phone_number": f"+{chosen_number}" if not chosen_number.startswith("+") else chosen_number,
        "provider_id": chosen_number,  # Plivo uses the number itself as the identifier
    }


async def release_number(provider_id: str):
    """Release (unrent) a phone number from Plivo."""
    # provider_id for Plivo is the phone number itself (without +)
    number = provider_id.lstrip("+")
    await _run_sync(client.numbers.delete, number=number)


# ────────────────────────────────────────────────────────────
# SMS
# ────────────────────────────────────────────────────────────

async def send_sms(
    from_number: str,
    to_number: str,
    body: str,
    media_url: str | None = None,
) -> dict:
    """Send an SMS/MMS via Plivo."""
    params = {
        "src": from_number,
        "dst": to_number,
        "text": body,
    }
    if media_url:
        params["media_urls"] = [media_url]
        params["type_"] = "mms"

    result = await _run_sync(client.messages.create, **params)

    # result is a tuple (status_code, response) or an object
    response = result[1] if isinstance(result, tuple) else result
    message_uuid = ""
    if hasattr(response, "message_uuid"):
        msg_uuids = response.message_uuid
        message_uuid = msg_uuids[0] if isinstance(msg_uuids, list) else msg_uuids
    elif isinstance(response, dict):
        msg_uuids = response.get("message_uuid", [])
        message_uuid = msg_uuids[0] if isinstance(msg_uuids, list) else msg_uuids

    return {
        "provider_message_id": message_uuid or "unknown",
        "status": "queued",
    }


# ────────────────────────────────────────────────────────────
# Outbound Calls
# ────────────────────────────────────────────────────────────

async def initiate_call(
    from_number: str,
    to_number: str,
    call_id: str,
) -> str:
    """
    Place an outbound call via Plivo.
    call_id is our internal ID, appended to the answer_url for webhook correlation.
    Returns the Plivo request_uuid (call identifier).
    """
    # answer_url tells Plivo what XML to fetch when the call is answered
    answer_url = f"{settings.BASE_URL}/plivo/answer/{call_id}"
    hangup_url = f"{settings.BASE_URL}/plivo/hangup/{call_id}"

    result = await _run_sync(
        client.calls.create,
        from_=from_number,
        to_=to_number,
        answer_url=answer_url,
        answer_method="POST",
        hangup_url=hangup_url,
        hangup_method="POST",
    )

    # result is a tuple (status_code, response)
    response = result[1] if isinstance(result, tuple) else result
    request_uuid = ""
    if hasattr(response, "request_uuid"):
        request_uuid = response.request_uuid
    elif isinstance(response, dict):
        request_uuid = response.get("request_uuid", "unknown")

    return request_uuid or "unknown"

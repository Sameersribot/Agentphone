"""
AgentLine — SignalWire Client
Wraps SignalWire REST API using httpx for US numbers.
"""

import httpx
import logging
from agentline.config import settings

logger = logging.getLogger(__name__)

def _get_auth():
    if not settings.SIGNALWIRE_PROJECT_ID or not settings.SIGNALWIRE_TOKEN:
        raise RuntimeError("SIGNALWIRE_PROJECT_ID and SIGNALWIRE_TOKEN must be set.")
    return (settings.SIGNALWIRE_PROJECT_ID, settings.SIGNALWIRE_TOKEN)

def _get_base_url():
    if not settings.SIGNALWIRE_SPACE_URL:
        raise RuntimeError("SIGNALWIRE_SPACE_URL must be set.")
    return f"https://{settings.SIGNALWIRE_SPACE_URL}/api/laml/2010-04-01/Accounts/{settings.SIGNALWIRE_PROJECT_ID}"

async def initiate_call(
    from_number: str,
    to_number: str,
    call_id: str,
) -> str:
    """
    Place an outbound voice call via SignalWire.

    When the callee answers, SignalWire will POST to our answer_url which returns
    XML with a <Response> element to start the voice pipeline.
    """
    answer_url = f"{settings.base_url_clean}/signalwire/answer/{call_id}"
    
    # We can also add status callback for hangup if needed
    # status_callback = f"{settings.base_url_clean}/signalwire/status/{call_id}"

    data = {
        "From": from_number,
        "To": to_number,
        "Url": answer_url,
        "Method": "POST",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{_get_base_url()}/Calls.json",
                auth=_get_auth(),
                data=data,
            )
            response.raise_for_status()
            resp_data = response.json()
            request_uuid = resp_data.get("sid", "unknown")
            logger.info("Outbound call initiated via SignalWire: %s → %s (sid: %s)", from_number, to_number, request_uuid)
            return request_uuid
    except httpx.HTTPStatusError as e:
        logger.error(f"SignalWire call failed: {e.response.text}")
        raise Exception(f"SignalWire call failed: {e.response.text}")
    except Exception as e:
        raise Exception(f"SignalWire call failed: {e}")

async def send_sms(
    from_number: str,
    to_number: str,
    body: str,
    media_url: str | None = None,
) -> dict:
    """
    Send an SMS or MMS message via SignalWire.
    """
    data = {
        "From": from_number,
        "To": to_number,
        "Body": body,
    }
    if media_url:
        data["MediaUrl"] = media_url

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{_get_base_url()}/Messages.json",
                auth=_get_auth(),
                data=data,
            )
            response.raise_for_status()
            resp_data = response.json()
            message_sid = resp_data.get("sid", "unknown")
            return {
                "provider_message_id": message_sid,
                "status": "queued",
            }
    except httpx.HTTPStatusError as e:
        logger.error(f"SignalWire SMS failed: {e.response.text}")
        raise Exception(f"SignalWire SMS failed: {e.response.text}")
    except Exception as e:
        raise Exception(f"SignalWire SMS failed: {e}")

async def provision_number(
    country: str = "US",
    number_type: str = "local",
    pattern: str | None = None,
    agent_id: str | None = None,
) -> dict:
    """
    Search for and buy a phone number from SignalWire's inventory.
    """
    if country.upper() != "US":
        raise Exception("SignalWire provisioning currently only configured for US.")

    search_url = f"{_get_base_url()}/AvailablePhoneNumbers/US/Local.json"
    params = {}
    if pattern:
        params["Contains"] = pattern

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Search
            resp = await client.get(search_url, auth=_get_auth(), params=params)
            resp.raise_for_status()
            data = resp.json()
            available = data.get("available_phone_numbers", [])
            
            if not available:
                raise Exception(f"No {number_type} numbers available in US matching pattern {pattern}")
            
            chosen_number = available[0]["phone_number"]

            # 2. Buy
            buy_url = f"{_get_base_url()}/IncomingPhoneNumbers.json"
            buy_payload = {"PhoneNumber": chosen_number}
            buy_resp = await client.post(buy_url, auth=_get_auth(), data=buy_payload)
            buy_resp.raise_for_status()
            buy_result = buy_resp.json()

            logger.info("Successfully provisioned via SignalWire: %s", chosen_number)

            return {
                "phone_number": chosen_number,
                "provider_id": buy_result.get("sid"),
            }
    except httpx.HTTPStatusError as e:
        raise Exception(f"SignalWire number provision failed: {e.response.text}")
    except Exception as e:
        raise Exception(f"SignalWire number provision failed: {e}")

async def release_number(provider_id: str):
    """
    Unrent a number from your SignalWire account.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.delete(
                f"{_get_base_url()}/IncomingPhoneNumbers/{provider_id}.json",
                auth=_get_auth()
            )
            resp.raise_for_status()
            logger.info("Released SignalWire number: %s", provider_id)
    except Exception as e:
        logger.warning("Failed to release SignalWire number %s: %s", provider_id, e)

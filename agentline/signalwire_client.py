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

    data = {
        "From": from_number,
        "To": to_number,
        "Url": answer_url,
        "Method": "POST",
        "StatusCallback": f"{settings.base_url_clean}/signalwire/hangup/{call_id}",
        "StatusCallbackMethod": "POST",
    }

    logger.info(
        "SignalWire call request: from=%s to=%s answer_url=%s",
        from_number, to_number, answer_url,
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
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
        error_body = e.response.text
        logger.error("SignalWire call failed (HTTP %s): %s", e.response.status_code, error_body)
        # Provide actionable hints for common errors
        hint = ""
        lower_err = error_body.lower()
        if "international" in lower_err or "permission" in lower_err or "geo" in lower_err:
            hint = " Enable international calling in SignalWire Dashboard → Settings → Permissions."
        elif "not verified" in lower_err or "unverified" in lower_err:
            hint = " Verify your caller ID in SignalWire Dashboard → Phone Numbers."
        elif "trial" in lower_err:
            hint = " Upgrade your SignalWire account from trial to enable outbound calls."
        raise Exception(f"SignalWire call failed: {error_body}{hint}")
    except Exception as e:
        logger.error("SignalWire call failed (non-HTTP): %s", e)
        raise Exception(f"SignalWire call failed: {e}")


async def hangup_call(provider_call_id: str) -> None:
    """
    Terminate a live call via SignalWire.

    Uses the Twilio-compatible API:
    POST /Accounts/{id}/Calls/{sid}.json  with Status=completed

    Args:
        provider_call_id: The SignalWire call SID
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{_get_base_url()}/Calls/{provider_call_id}.json",
                auth=_get_auth(),
                data={"Status": "completed"},
            )
            response.raise_for_status()
            logger.info("SignalWire hangup successful: %s", provider_call_id)
    except httpx.HTTPStatusError as e:
        logger.warning("SignalWire hangup failed for %s: %s", provider_call_id, e.response.text)
        raise Exception(f"SignalWire hangup failed: {e.response.text}")
    except Exception as e:
        logger.warning("SignalWire hangup failed for %s: %s", provider_call_id, e)
        raise Exception(f"SignalWire hangup failed: {e}")


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
    Automatically configures Voice URL and SMS URL so inbound calls
    are routed to our server.
    """
    if country.upper() != "US":
        raise Exception("SignalWire provisioning currently only configured for US.")

    search_url = f"{_get_base_url()}/AvailablePhoneNumbers/US/Local.json"
    params = {}
    if pattern:
        params["Contains"] = pattern

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Search
            resp = await client.get(search_url, auth=_get_auth(), params=params)
            resp.raise_for_status()
            data = resp.json()
            available = data.get("available_phone_numbers", [])
            
            if not available:
                raise Exception(f"No {number_type} numbers available in US matching pattern {pattern}")
            
            chosen_number = available[0]["phone_number"]

            # 2. Buy + configure webhook URLs for inbound routing
            buy_url = f"{_get_base_url()}/IncomingPhoneNumbers.json"
            buy_payload = {
                "PhoneNumber": chosen_number,
                "VoiceUrl": f"{settings.base_url_clean}/signalwire/inbound",
                "VoiceMethod": "POST",
                "SmsUrl": f"{settings.base_url_clean}/signalwire/sms",
                "SmsMethod": "POST",
                "StatusCallback": f"{settings.base_url_clean}/signalwire/hangup/inbound_status",
                "StatusCallbackMethod": "POST",
            }
            buy_resp = await client.post(buy_url, auth=_get_auth(), data=buy_payload)
            buy_resp.raise_for_status()
            buy_result = buy_resp.json()

            logger.info(
                "Provisioned %s via SignalWire (SID: %s) — Voice URL: %s",
                chosen_number, buy_result.get("sid"),
                f"{settings.base_url_clean}/signalwire/inbound",
            )

            return {
                "phone_number": chosen_number,
                "provider_id": buy_result.get("sid"),
            }
    except httpx.HTTPStatusError as e:
        raise Exception(f"SignalWire number provision failed: {e.response.text}")
    except Exception as e:
        raise Exception(f"SignalWire number provision failed: {e}")


async def configure_number_webhooks(provider_id: str) -> None:
    """
    Update webhook URLs on an existing SignalWire number.
    Use this for numbers that were bought manually or need re-configuration.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_get_base_url()}/IncomingPhoneNumbers/{provider_id}.json",
                auth=_get_auth(),
                data={
                    "VoiceUrl": f"{settings.base_url_clean}/signalwire/inbound",
                    "VoiceMethod": "POST",
                    "SmsUrl": f"{settings.base_url_clean}/signalwire/sms",
                    "SmsMethod": "POST",
                },
            )
            resp.raise_for_status()
            logger.info("Configured webhook URLs for SignalWire number: %s", provider_id)
    except Exception as e:
        logger.warning("Failed to configure webhooks for %s: %s", provider_id, e)

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

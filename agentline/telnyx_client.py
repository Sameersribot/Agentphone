"""
AgentLine — Telnyx Client
Wraps Telnyx SDK for number provisioning, SMS, and call initiation.
"""

import telnyx
from agentline.config import settings

telnyx.api_key = settings.TELNYX_API_KEY


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
        "filter[country_code]": country,
        "filter[features]": ["sms", "voice"],
    }
    if area_code:
        params["filter[national_destination_code]"] = area_code

    numbers = telnyx.AvailablePhoneNumber.list(**params)
    if not numbers.data:
        raise Exception(f"No numbers available in {country} {area_code or ''}")

    chosen = numbers.data[0].phone_number

    # Purchase the number and bind it to our TeXML application
    order = telnyx.NumberOrder.create(
        phone_numbers=[{"phone_number": chosen}],
        connection_id=settings.TELNYX_CONNECTION_ID,
    )

    return {
        "phone_number": chosen,
        "telnyx_id": order.phone_numbers[0].id,
    }


async def release_number(telnyx_id: str):
    """Release a phone number back to Telnyx."""
    telnyx.PhoneNumber.retrieve(telnyx_id).delete()


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

    result = telnyx.Message.create(**params)
    return {
        "telnyx_message_id": result.id,
        "status": result.to[0].get("status", "queued") if result.to else "queued",
    }


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
    call = telnyx.Call.create(
        connection_id=settings.TELNYX_CONNECTION_ID,
        from_=from_number,
        to=to_number,
        client_state=call_id,
        webhook_url=f"{settings.BASE_URL}/telnyx/voice",
    )
    return call.call_control_id

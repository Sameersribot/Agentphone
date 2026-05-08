"""
Test Agent Loop — Simulates an external AI agent talking through AgentLine.

This script:
1. Creates an outbound call via POST /v1/calls
2. Long-polls GET /v1/calls/{id}/listen?wait=true to hear what the person says
3. Sends replies via POST /v1/calls/{id}/speak

Run this while the AgentLine server is running to test the full voice relay flow.

Usage:
    python test_agent_loop.py --to +919XXXXXXXXX
    python test_agent_loop.py --to +919XXXXXXXXX --base-url https://agentphone-production.up.railway.app
"""

import argparse
import httpx
import time
import sys
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("AGENTLINE_API_KEY", "")
AGENT_ID = os.getenv("AGENTLINE_AGENT_ID", "")
BASE_URL = os.getenv("AGENTLINE_URL", "http://localhost:8000")


def main():
    parser = argparse.ArgumentParser(description="Test agent that talks on a call")
    parser.add_argument("--to", required=True, help="Phone number to call (E.164, e.g. +919876543210)")
    parser.add_argument("--base-url", default=BASE_URL, help="AgentLine server URL")
    parser.add_argument("--api-key", default=API_KEY, help="AgentLine API key")
    parser.add_argument("--agent-id", default=AGENT_ID, help="Agent ID")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: Set AGENTLINE_API_KEY in .env or pass --api-key")
        sys.exit(1)
    if not args.agent_id:
        print("ERROR: Set AGENTLINE_AGENT_ID in .env or pass --agent-id")
        sys.exit(1)

    base = args.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }

    print(f"\n{'='*60}")
    print(f"  AgentLine Test Agent")
    print(f"  Server:   {base}")
    print(f"  Agent ID: {args.agent_id}")
    print(f"  Calling:  {args.to}")
    print(f"{'='*60}\n")

    # Step 1: Create the call
    print("[1] Creating outbound call...")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{base}/v1/calls", headers=headers, json={
            "agent_id": args.agent_id,
            "to_number": args.to,
        })
        if resp.status_code != 200:
            print(f"    FAILED: {resp.status_code} — {resp.text}")
            sys.exit(1)

        call_data = resp.json()
        call_id = call_data["id"]
        print(f"    Call created: {call_id}")
        print(f"    From: {call_data['from_number']} → To: {call_data['to_number']}")
        print(f"    Status: {call_data['status']}")
        print()

    # Step 2: Enter the listen/speak loop
    print("[2] Entering listen/speak loop (Ctrl+C to quit)...")
    print("    Waiting for the person to answer and speak...\n")

    turn_index = 0  # Track which transcript entries we've seen

    with httpx.Client(timeout=60.0) as client:
        while True:
            try:
                # Long-poll for new speech (waits up to 25 seconds)
                resp = client.get(
                    f"{base}/v1/calls/{call_id}/listen",
                    headers=headers,
                    params={"wait": "true", "after": str(turn_index)},
                )

                if resp.status_code == 404:
                    print("    Call not found — may have ended.")
                    break

                data = resp.json()
                status = data.get("status", "unknown")

                # Check for new speech entries
                new_entries = data.get("new_entries", [])
                total_turns = data.get("total_turns", 0)

                if new_entries:
                    for entry in new_entries:
                        role = entry.get("role", "?")
                        text = entry.get("text", "")
                        turn_index += 1

                        if role == "human":
                            print(f"    🎤 Person said: \"{text}\"")

                            # Generate a response (your real agent would use AI here)
                            response = generate_response(text, turn_index)
                            print(f"    🤖 Agent says:  \"{response}\"")

                            # Send the response via /speak
                            speak_resp = client.post(
                                f"{base}/v1/calls/{call_id}/speak",
                                headers=headers,
                                json={"text": response},
                            )
                            if speak_resp.status_code == 200:
                                print(f"    ✅ Response queued — Plivo will speak it within ~2 seconds")
                            else:
                                print(f"    ❌ Speak failed: {speak_resp.status_code} — {speak_resp.text}")
                            print()

                        elif role == "agent":
                            # This was our own response, skip
                            pass

                # Call ended
                if status == "completed":
                    print(f"\n    📞 Call ended. Total turns: {total_turns}")
                    break

            except httpx.ReadTimeout:
                # Long-poll timed out, loop again
                continue
            except KeyboardInterrupt:
                print("\n\n    Stopped by user.")
                break
            except Exception as e:
                print(f"\n    Error: {e}")
                time.sleep(2)

    print("\n✅ Test complete.")


def generate_response(human_text: str, turn: int) -> str:
    """
    Simple echo-back response for testing.
    Replace this with your actual AI agent logic.
    """
    lower = human_text.lower()

    if any(word in lower for word in ["hello", "hi", "hey"]):
        return "Hello! I'm your AI agent. How can I help you today?"
    elif any(word in lower for word in ["bye", "goodbye", "end"]):
        return "Thank you for talking with me. Goodbye!"
    elif "name" in lower:
        return "I'm an AI assistant created by AgentLine. What's your name?"
    elif any(word in lower for word in ["help", "support"]):
        return "I'd be happy to help! What do you need assistance with?"
    elif "?" in human_text:
        return f"That's a great question. You asked: {human_text}. Let me think about that."
    else:
        return f"I heard you say: {human_text}. Is there anything specific you'd like to discuss?"


if __name__ == "__main__":
    main()

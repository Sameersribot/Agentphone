import asyncio
import os
from dotenv import load_dotenv
from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

load_dotenv()

async def main():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    print("Using Deepgram API key:", api_key[:10] + "..." if api_key else "None")
    client = DeepgramClient(api_key)
    live = client.listen.asyncwebsocket.v("1")

    async def on_open(self, open_response, **kwargs):
        print("Deepgram WebSocket OPEN:", open_response)

    async def on_transcript(self, result, **kwargs):
        alt = result.channel.alternatives[0]
        if alt.transcript:
            print(f"Transcript (is_final={result.is_final}, speech_final={result.speech_final}): {alt.transcript}")

    async def on_error(self, error, **kwargs):
        print("Deepgram ERROR:", error)

    async def on_close(self, *args, **kwargs):
        print("Deepgram CLOSE")

    live.on(LiveTranscriptionEvents.Open, on_open)
    live.on(LiveTranscriptionEvents.Transcript, on_transcript)
    live.on(LiveTranscriptionEvents.Error, on_error)
    live.on(LiveTranscriptionEvents.Close, on_close)

    options = LiveOptions(
        model="nova-2-phonecall",
        language="en-US",
        smart_format=True,
        interim_results=True,
        utterance_end_ms=1000,
        endpointing=500,
        vad_events=True,
        encoding="mulaw",
        sample_rate=8000,
    )

    print("Starting Deepgram connection (asyncwebsocket)...")
    try:
        result = await live.start(options)
        print(f"start() returned: {result}")
        # Send some silent mulaw audio to verify send() works
        silent_frame = b'\xff' * 160  # 160 bytes of mulaw silence
        await live.send(silent_frame)
        print("Sent 160 bytes of silent audio — no send error")
        await asyncio.sleep(3)
        print("Finishing Deepgram connection...")
        await live.finish()
        print("Success!")
    except Exception as e:
        print("Failed:", type(e).__name__, e)

if __name__ == "__main__":
    asyncio.run(main())

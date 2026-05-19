import asyncio
import os
from dotenv import load_dotenv
from deepgram import DeepgramClient, LiveOptions, LiveTranscriptionEvents

load_dotenv()

async def main():
    api_key = os.getenv("DEEPGRAM_API_KEY")
    print("Using Deepgram API key:", api_key[:10] + "..." if api_key else "None")
    client = DeepgramClient(api_key)
    live = client.listen.asynclive.v("1")
    
    async def on_transcript(self, result, **kwargs):
        print("Transcript:", result)
        
    live.on(LiveTranscriptionEvents.Transcript, on_transcript)
    
    options = LiveOptions(
        model="nova-2-phonecall",
        language="en-US",
        smart_format=True,
        interim_results=False,
        endpointing=500,
        vad_events=True,
        encoding="mulaw",
        sample_rate=8000,
    )
    
    print("Starting Deepgram live connection...")
    try:
        await live.start(options)
        print("Deepgram live connection started successfully!")
        await asyncio.sleep(2)
        print("Finishing Deepgram live connection...")
        await live.finish()
        print("Success!")
    except Exception as e:
        print("Failed to connect/start:", e)

if __name__ == "__main__":
    asyncio.run(main())

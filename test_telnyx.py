import telnyx
import asyncio
import inspect

async def test():
    client = telnyx.AsyncTelnyx(api_key='test')
    print("Methods on calls:")
    print([m for m in dir(client.calls) if not m.startswith('_')])
    
    print("\nActions on calls:")
    print([m for m in dir(client.calls.actions) if not m.startswith('_')])

if __name__ == "__main__":
    asyncio.run(test())

import plivo
import asyncio

async def test():
    client = plivo.RestClient('test_id', 'test_token')
    print("Methods on numbers:")
    print([m for m in dir(client.numbers) if not m.startswith('_')])
    
    print("\nMethods on calls:")
    print([m for m in dir(client.calls) if not m.startswith('_')])
    
    print("\nMethods on messages:")
    print([m for m in dir(client.messages) if not m.startswith('_')])

if __name__ == "__main__":
    asyncio.run(test())

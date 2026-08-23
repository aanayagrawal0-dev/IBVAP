import asyncio
import json
import websockets


async def main():
    uri = "ws://localhost:8000/ws/alerts"
    async with websockets.connect(uri) as ws:
        print("Connected. Waiting up to 25s for alerts...")
        try:
            for _ in range(5):
                msg = await asyncio.wait_for(ws.recv(), timeout=25)
                print("ALERT:", json.dumps(json.loads(msg), indent=2))
        except asyncio.TimeoutError:
            print("No alert received within timeout.")


asyncio.run(main())

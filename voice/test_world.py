import asyncio
import aiohttp
from reflection import ReflectionEngine
from config import Config


async def main():
    c = Config.load()
    e = ReflectionEngine(c, None)

    world = await e._fetch_world()

    print("Weather:", world.get("weather"))
    print("Cricket live:", world.get("cricket_live"))
    for s in world.get("searches", []):
        print(f"[{s['query']}]:", s["snippet"])

    print("\nPhone ping:", await e._ping_phone())


asyncio.run(main())

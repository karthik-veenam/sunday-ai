import asyncio
import json
import memory
from reflection import ReflectionEngine
from config import Config
from home import HAClient


async def main():
    memory.init()
    c = Config.load()
    ha = HAClient(c.ha_url, c.ha_token) if c.ha_token else None
    e = ReflectionEngine(c, None, ha_client=ha)
    ctx = await e.build_reflection_context()
    print(json.dumps(ctx, indent=2))


asyncio.run(main())

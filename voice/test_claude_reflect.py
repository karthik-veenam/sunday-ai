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

    context = await e.build_reflection_context()
    print("=== Context ===")
    print(json.dumps(context, indent=2, ensure_ascii=False))

    print("\n=== Claude Reflection ===")
    result = await e.claude_reflect(context)
    print(json.dumps(result, indent=2, ensure_ascii=False))


asyncio.run(main())

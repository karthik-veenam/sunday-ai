import asyncio
import aiohttp

HOST = "192.168.88.35"
HEADERS = {"X-Api-Key": "0987654321", "User-Agent": "okhttp/4.10.0"}


async def main():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
        # Step 1: wake the FireTVRemote companion app via DIAL (port 8009)
        try:
            async with s.post(f"http://{HOST}:8009/apps/FireTVRemote") as r:
                print("DIAL wake HTTP:", r.status)
        except Exception as e:
            print("DIAL wake failed:", repr(e))

        # Poll port 8080 until the HTTPS remote-control service is actually up
        ready = False
        for attempt in range(20):
            await asyncio.sleep(1)
            try:
                async with s.get(f"https://{HOST}:8080/v1/FireTV/appsV2", ssl=False, headers=HEADERS) as r:
                    print("readiness check", attempt, "->", r.status)
                    ready = True
                    break
            except Exception as e:
                print("readiness check", attempt, "not ready:", repr(e))

        if not ready:
            print("Service never came up on port 8080.")
            return

        # Step 2: ask the device to display a pairing PIN on screen
        for path in ["/v1/FireTV/pin/display", "/v1/FireTV/pin/start", "/v1/FireTV/pin"]:
            try:
                async with s.post(f"https://{HOST}:8080{path}", ssl=False, headers=HEADERS, json={}) as r:
                    text = await r.text()
                    print(path, "->", r.status, text[:200])
            except Exception as e:
                print(path, "-> failed:", repr(e))


asyncio.run(main())

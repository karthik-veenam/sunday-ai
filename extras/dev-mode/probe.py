import asyncio, json, os, aiohttp
SOCK = os.path.expanduser("~/.codex/app-server-control/app-server-control.sock")
async def main():
    async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=SOCK)) as s:
        async with s.ws_connect("http://localhost/") as ws:
            await ws.send_json({"id":1,"method":"initialize","params":{"clientInfo":{"name":"sunday-dev-mode","title":"Sunday Dev Mode","version":"0.1"}}})
            print(json.dumps(await asyncio.wait_for(ws.receive_json(),5))[:600])
            await ws.send_json({"method":"initialized"})
            await ws.send_json({"id":2,"method":"thread/list","params":{"limit":3}})
            print(json.dumps(await asyncio.wait_for(ws.receive_json(),5))[:900])
asyncio.run(main())

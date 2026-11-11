import asyncio,json
from pathlib import Path
import aiohttp
async def main():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
  async with s.ws_connect('ws://localhost:8123/api/websocket') as w:
   await w.receive_json();await w.send_json({'type':'auth','access_token':token});assert (await w.receive_json())['type']=='auth_ok'
   await w.send_json({'id':1,'type':'media_player/browse_media','entity_id':'media_player.bravia_kd_65x75k','media_content_type':'apps','media_content_id':'apps'})
   while True:
    r=await w.receive_json()
    if r.get('id')==1:break
   assert r.get('success')
   folder=Path('/home/djpi/ha-assets/remote-apps');folder.mkdir(parents=True,exist_ok=True)
   apps=[]
   for a in r['result']['children']:
    if a['title'] not in ['YouTube','JioHotstar']:continue
    name='youtube' if a['title']=='YouTube' else 'jiohotstar'
    async with s.get('http://localhost:8123'+a['thumbnail'],headers={'Authorization':'Bearer '+token}) as response:
     response.raise_for_status();blob=await response.read();assert response.content_type.startswith('image/')
    (folder/(name+'.png')).write_bytes(blob)
    apps.append({'name':a['title'],'id':a['media_content_id'],'image':'/local/remote-apps/'+name+'.png'})
    print('Saved official TV app artwork:',name,len(blob),'bytes')
   assert len(apps)==2
   (folder/'apps.json').write_text(json.dumps(apps,indent=2))
asyncio.run(main())

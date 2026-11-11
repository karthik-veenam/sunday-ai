import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,'/home/pi/homeassistant/custom_components/haismart/vendor')
from haismart_hrdp.uss import async_read_status,parse_full_status
from haismart_hrdp.device_model import model_for
async def main():
 entries=json.load(open('/home/pi/homeassistant/.storage/core.config_entries'))['data']['entries']
 d=next(e['data'] for e in entries if e['domain']=='haismart' and e['title']=='Fridge')
 blobs=await async_read_status(d['host'],d['device_id'],d['local_key'],timeout=8,expect_localkey_version=d.get('localkey_version') or None)
 p=Path('/home/djpi/ha-backups/fridge-readonly-frames.json');p.touch(mode=0o600);p.write_text(json.dumps([b.hex() for b in blobs]))
 model=model_for(d['uplus_id']);declared=frozenset(a['name'] for a in json.loads(d['digital_model'])['attributes'])
 print('Reports received:',len(blobs),'Manufacturer map found:',model is not None)
 for b in blobs:
  try:print('AC decoder:',bool(parse_full_status(b,uplus_id=d['uplus_id'])))
  except ValueError as e:print('Reproduced error:',str(e))
  if model:
   decoded=model.decode(b,only=declared)
   print('Manufacturer fields:',decoded)
asyncio.run(main())

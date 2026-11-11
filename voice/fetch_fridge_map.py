import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,'/home/pi/homeassistant/custom_components/haismart/vendor')
from haismart_extractor.device_config import async_fetch_device_config
from haismart_hrdp.device_model import project_config,model_from_record
async def main():
 entries=json.load(open('/home/pi/homeassistant/.storage/core.config_entries'))['data']['entries']
 d=next(e['data'] for e in entries if e['domain']=='haismart' and e['title']=='Fridge')
 config=await async_fetch_device_config(d['uplus_id'])
 assert config,'Manufacturer returned no map'
 record=project_config(config,d['uplus_id']);assert record,'Map projection failed'
 Path('/home/djpi/ha-backups/fridge-device-map.json').write_text(json.dumps(record))
 model=model_from_record(d['uplus_id'],record)
 declared=frozenset(a['name'] for a in json.loads(d['digital_model'])['attributes'])
 print('Published map fields:',len(record['fields']))
 for h in json.load(open('/home/djpi/ha-backups/fridge-readonly-frames.json')):
  print('Decoded:',model.decode(bytes.fromhex(h),only=declared))
asyncio.run(main())

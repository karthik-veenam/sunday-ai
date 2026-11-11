"""Read-only device schema lookup through the already configured Tuya project."""
import json,tinytuya
c=json.load(open('/home/djpi/sunday/config.json'))
assert c.get('tuya_api_key') and c.get('tuya_api_secret'),'Existing Tuya API credentials not configured'
cloud=tinytuya.Cloud(apiRegion=c.get('tuya_region','in'),apiKey=c['tuya_api_key'],apiSecret=c['tuya_api_secret'],apiDeviceID=c.get('tuya_geyser_id'))
lock='d70fc7c25661ad0361efm5'
for operation in ['getstatus','getfunctions','getproperties']:
 try:
  result=getattr(cloud,operation)(lock)
  if not isinstance(result,dict):print(operation,'Unexpected response');continue
  print(operation,{k:result.get(k) for k in ['success','code','msg','Error','Err'] if k in result})
  if result.get('success'):
   data=result.get('result')
   if isinstance(data,list):print('Exposed codes:',[v.get('code') for v in data if isinstance(v,dict)])
   elif isinstance(data,dict):
    print('Category:',data.get('category'))
    for key in ['functions','status']:
     print(key,[{k:v.get(k) for k in ['code','type','values']} for v in data.get(key,[])])
 except Exception as e:print(operation,type(e).__name__)

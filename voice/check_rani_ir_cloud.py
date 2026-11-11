import json,tinytuya
c=json.load(open('/home/djpi/sunday/config.json'))
cloud=tinytuya.Cloud(apiRegion=c.get('tuya_region','in'),apiKey=c['tuya_api_key'],apiSecret=c['tuya_api_secret'],apiDeviceID=c['tuya_geyser_id'])
for url in ['/v2.0/infrareds/d7e285658f1b31d4c5z3ot/remotes','/v2.0/infrareds/d7e285658f1b31d4c5z3ot/remotes/d75a423c7fd8bd9987hv1k/ac/status']:
 r=cloud.cloudrequest(url)
 print(url,json.dumps(r))

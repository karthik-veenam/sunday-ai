import sys,asyncio,json
sys.path.insert(0,'/home/djpi/sunday')
from central_ac_inching import HA
async def main():
 async with HA() as h:
  c=await h.call('lovelace/config',url_path='sunday-hub')
  def walk(x,path=''):
   if isinstance(x,dict):
    if x.get('entity')=='input_boolean.sunday_transcripts_to_telegram':print(path,json.dumps(x))
    for k,v in x.items():walk(v,path+'/'+str(k))
   elif isinstance(x,list):
    for i,v in enumerate(x):walk(v,path+'/'+str(i))
  walk(c)
asyncio.run(main())

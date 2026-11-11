import time,json,socket
from zeroconf import Zeroconf,ServiceBrowser,ServiceListener
class Listener(ServiceListener):
 def add_service(self,z,t,n):
  info=z.get_service_info(t,n,timeout=1500)
  if info:
   props={k.decode(errors='replace'):v.decode(errors='replace') if isinstance(v,bytes) else v for k,v in info.properties.items() if k in [b'fn',b'md',b'manufacturer',b'name',b'model']}
   print(json.dumps({'service':t,'name':n,'addresses':info.parsed_addresses(),'port':info.port,'properties':props}),flush=True)
 def update_service(self,*args):pass
 def remove_service(self,*args):pass
z=Zeroconf();listeners=[ServiceBrowser(z,t,Listener()) for t in ['_androidtvremote2._tcp.local.','_googlecast._tcp.local.']]
time.sleep(5);z.close()

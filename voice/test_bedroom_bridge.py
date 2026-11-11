import asyncio,importlib.util,json
from pathlib import Path
import hogar_mqtt_bridge as bridge
spec=importlib.util.spec_from_file_location('bridge_before','/home/djpi/ha-backups/hogar_mqtt_bridge.before-bedroom-expansion.py');before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
class MQTT:
 def __init__(self):self.messages={}
 def publish(self,topic,payload,retain=False):self.messages[topic]=json.loads(payload)
old=MQTT();new=MQTT();before.mq=old;bridge.mq=new;before.publish_discovery();bridge.publish_discovery()
assert all(new.messages[t]==p for t,p in old.messages.items()),'Existing discovery changed'
assert len(new.messages)-len(old.messages)==11
assert len(bridge.ALL_SUFFIXES)==len(set(bridge.ALL_SUFFIXES))
assert set(bridge.ALL_SUFFIXES)==set(json.load(open('/home/djpi/sunday/house_layout.json'))['devices'])
class Hub:
 def __init__(self):self.commands=[]
 async def set_device(self,name,**kwargs):self.commands.append((name,kwargs))
async def main():
 bridge._loop=asyncio.get_running_loop();hub=Hub();bridge._hogar=hub
 tests=[('homeassistant/light/sian_10_1/set','ON',('10-1',{'on':True})),('homeassistant/light/rani_11_7/brightness/set','57',('11-7',{'on':True,'brightness':57})),('homeassistant/fan/sian_10_9/preset_set','high',('10-9',{'on':True,'speed':3})),('homeassistant/fan/rani_11_9/set','OFF',('11-9',{'on':False}))]
 from types import SimpleNamespace
 for topic,payload,expected in tests:
  bridge._on_message(None,None,SimpleNamespace(topic=topic,payload=payload.encode()))
  await asyncio.sleep(.01)
  assert hub.commands[-1]==expected,(hub.commands,expected)
 print('PASS: 11 new entities; all existing discovery unchanged; complete room coverage; new light/dimmer/fan command routing verified with fake hub.')
asyncio.run(main())

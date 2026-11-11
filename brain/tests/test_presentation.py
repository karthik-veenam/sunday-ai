import unittest
from types import SimpleNamespace
from home_presentation import devices
from text_presentation import telegram_text

class Tests(unittest.TestCase):
    def model(self):
        return SimpleNamespace(areas={'room':{'name':'Bedroom'}},devices={'hub':{'name':"Roka's Room"},'sensor':{'name':'Hardware model 123'},'button':{'name_by_user':'Night Button'}},household={'area_names':{'room':['RoKa','our room']}},programs={'a':{'source':{'triggers':[{'device_id':'button'}]}}})
    def entity(self,eid,name,did=None,**kw):
        return {'entity_id':eid,'name':name,'device_id':did,'area_id':'room','domain':eid.split('.')[0],'state':'off','available':True,'attributes':{},**kw}
    def test_separate_hub_outputs_and_hidden_internal_entities(self):
        m=self.model();rows=[self.entity('light.a',"Roka's Room Roka Cove",'hub'),self.entity('fan.b',"Roka's Room Roka Fan",'hub'),self.entity('update.firmware','Firmware','hub',entity_category='diagnostic'),self.entity('climate.helper','Bedroom Auto Target',platform='generic_thermostat'),self.entity('input_boolean.mode','Mode')]
        result=devices(m,rows)
        self.assertEqual([x['name'] for x in result],['Cove','Fan'])
        self.assertEqual([x['entity_ids'] for x in result],[['light.a'],['fan.b']])
    def test_environment_readings_grouped_without_counting_battery(self):
        m=self.model();rows=[self.entity('sensor.temp','Temperature','sensor',attributes={'device_class':'temperature','unit_of_measurement':'°C'},state='26'),self.entity('sensor.hum','Humidity','sensor',attributes={'device_class':'humidity'},state='50'),self.entity('sensor.bat','Battery','sensor',attributes={'device_class':'battery'})]
        result=devices(m,rows);self.assertEqual(len(result),1);self.assertEqual(len(result[0]['readings']),2);self.assertEqual(result[0]['name'],'Room sensor');self.assertEqual([x['entity_id'] for x in result[0]['readings']],['sensor.temp','sensor.hum'])
    def test_button_comes_from_real_trigger_relationship(self):
        m=self.model();rows=[self.entity('sensor.button_battery','Battery','button',entity_category='diagnostic')]
        self.assertEqual(devices(m,rows)[0]['name'],'Night Button')
        m.programs={};self.assertEqual(devices(m,rows),[])
    def test_unavailable_real_light_remains_visible(self):
        result=devices(self.model(),[self.entity('light.a','Lamp',available=False,state='unavailable')]);self.assertFalse(result[0]['available'])
    def test_plain_telegram_without_broken_markers(self):
        self.assertEqual(telegram_text('## Lights\n**Moon** and **Dashboard** are on. Panels *(unavailable)*.'),'Lights\nMoon and Dashboard are on. Panels (unavailable).')
        self.assertEqual(telegram_text('Use `light.moon`; 2 * 3 = 6; light.foo__bar__.'),'Use light.moon; 2 * 3 = 6; light.foo__bar__.')
        self.assertEqual(telegram_text('[Source](https://example.com/a)'),'Source (https://example.com/a)')
        self.assertEqual(telegram_text('Use `__init__` and ```literal```.'),'Use __init__ and literal.')
        self.assertEqual(telegram_text('```python\nprint(2 ** 3)\n```'),'print(2 ** 3)')

    def test_primary_plug_label_and_assistant_device_grouping(self):
        m=self.model();m.devices.update({'plug':{'name':'Projector '},'assistant':{'name':'Sunday'},'old':{'name':'Legacy voice service','entry_type':'service'}})
        rows=[self.entity('switch.plug','Projector  Socket 1','plug'),self.entity('media_player.speaker','Sunday Media Player','assistant'),self.entity('assist_satellite.main','Sunday Assist satellite','assistant'),self.entity('assist_satellite.old','Legacy voice service','old')]
        result=devices(m,rows);self.assertEqual([(x['name'],x['kind']) for x in result],[('Projector','switch'),('Sunday','voice assistant')])

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_human_view_and_explicit_entity_view(self):
        from test_rebuild import HA
        from home_model import HomeModel
        import home_tools,json
        from unittest.mock import patch
        h=HA();m=HomeModel(h,{'primary_area':'bedroom'});await m.refresh()
        m.entities['input_boolean.helper']={'entity_id':'input_boolean.helper','name':'Internal mode','domain':'input_boolean','area_id':'bedroom','aliases':[],'available':True,'state':'on','attributes':{}}
        with patch.object(home_tools,'_model',m):
            human=json.loads(await home_tools.execute('home_query',{'operation':'discover','query':'devices in our room'}))
            raw=json.loads(await home_tools.execute('home_query',{'operation':'discover','query':'devices in our room','view':'entities'}))
        self.assertNotIn('Internal mode',json.dumps(human));self.assertIn('Internal mode',json.dumps(raw));self.assertNotIn('total',human)
    async def test_present_tense_context_gets_changed_state_without_registry_refresh(self):
        from test_rebuild import HA
        from home_model import HomeModel
        import json
        h=HA();m=HomeModel(h,{'primary_area':'bedroom'});await m.refresh();h.on=True
        data=json.loads(await m.context('devices in our room'))
        self.assertTrue(all(e['state']=='on' for e in data['relevant_devices']))
    async def test_telegram_send_normalizes_markdown_without_changing_send_policy(self):
        from test_rebuild import FakeRuntime,API,update
        from telegram_transport import TelegramTransport
        import tempfile
        from pathlib import Path
        r=FakeRuntime()
        async def turn(request,emit):await emit({'type':'final','text':'**Moon** is on.'})
        r.turn=turn;api=API()
        with tempfile.TemporaryDirectory() as d:
            t=TelegramTransport(r,Path(d)/'telegram.db',api)
            try:
                await t.handle(update());await t.handle(update())
                self.assertEqual(len(api.calls),1);self.assertEqual(api.calls[0][1]['text'],'Moon is on.')
            finally:await t.close()

class ExplicitPhysicalFacadeTests(unittest.TestCase):
    def test_opted_in_template_is_visible_but_internal_and_diagnostic_stay_hidden(self):
        from home_presentation import ordinary
        e={'platform':'template','everyday_device':True}
        self.assertTrue(ordinary(e))
        self.assertFalse(ordinary({'platform':'template'}))
        self.assertFalse(ordinary({**e,'hidden_by':'user'}))
        self.assertFalse(ordinary({**e,'entity_category':'diagnostic'}))

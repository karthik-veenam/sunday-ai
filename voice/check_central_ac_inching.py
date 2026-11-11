import asyncio,json,time
from central_ac_inching import HA
from playwright.async_api import async_playwright
async def lifecycle():
 async with HA() as h:
  states={s['entity_id']:s for s in await h.call('get_states')};assert states['input_boolean.central_ac_inching']['state']=='off';assert states['climate.hall_ac']['state'] not in ['off','unknown','unavailable']
  try:
   await h.service('input_boolean','turn_on',{'entity_id':'input_boolean.central_ac_inching'});await asyncio.sleep(.6)
   states={s['entity_id']:s for s in await h.call('get_states')};assert states['input_boolean.central_ac_inching_armed']['state']=='on';first=states['input_datetime.central_ac_inching_deadline']['attributes']['timestamp'];assert 3580<first-time.time()<3610
   await h.service('input_number','set_value',{'entity_id':'input_number.central_ac_inching_minutes','value':61});await asyncio.sleep(.6)
   states={s['entity_id']:s for s in await h.call('get_states')};second=states['input_datetime.central_ac_inching_deadline']['attributes']['timestamp'];assert 59<=second-first<=63
  finally:
   await h.service('input_boolean','turn_off',{'entity_id':'input_boolean.central_ac_inching'});await h.service('input_number','set_value',{'entity_id':'input_number.central_ac_inching_minutes','value':60})
  await asyncio.sleep(.5);states={s['entity_id']:s for s in await h.call('get_states')};assert states['input_boolean.central_ac_inching_armed']['state']=='off';assert states['climate.hall_ac']['state']=='cool'
  print('Live automation: enabling arms a 60-minute deadline, duration change resets it, disabling cancels. AC left running unchanged.')
async def ui():
 token=json.load(open('/home/djpi/sunday/config.json'))['ha_token']
 async with async_playwright() as p:
  b=await p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=await b.new_context(viewport={'width':400,'height':1000},device_scale_factor=1)
  await c.add_init_script("if(location.origin==='http://127.0.0.1:8123')localStorage.setItem('hassTokens',JSON.stringify({hassUrl:location.origin,access_token:"+json.dumps(token)+",token_type:'Bearer',expires:Date.now()+3600000,expires_in:3600,clientId:location.origin+'/'}));")
  page=await c.new_page();await page.goto('http://127.0.0.1:8123/dashboard-roka/hall',wait_until='domcontentloaded');await page.wait_for_timeout(13000)
  result=await page.evaluate('''()=>{let errors=[],target;function w(r){for(const e of r.querySelectorAll('*')){if(e.tagName==='HUI-ERROR-CARD')errors.push(e.shadowRoot?.textContent);if(e.tagName==='MUSHROOM-TEMPLATE-CHIP'&&e._config?.entity==='input_boolean.central_ac_inching')target=e;if(e.shadowRoot)w(e.shadowRoot)}}w(document);if(!target)return{errors,found:false};window.timerChip=target;let r=target.getBoundingClientRect();return{errors,found:true,rect:{x:r.x,y:r.y,width:r.width,height:r.height}}}''');print('Dashboard',result);assert result['found'] and not result['errors'];assert result['rect']['height']>=38
  await page.screenshot(path='/tmp/central-ac-timer-hall.png')
  await page.mouse.click(result['rect']['x']+19,result['rect']['y']+19);await page.wait_for_timeout(1200)
  popup=page.locator('central-ac-inching-card');await popup.wait_for(state='visible',timeout=5000);assert await popup.get_by_label('Hours',exact=True).input_value()=='1';assert await popup.get_by_label('Minutes',exact=True).input_value()=='0';assert not await popup.get_by_label('Enable automatic turn off').is_checked();await page.screenshot(path='/tmp/central-ac-timer-popup.png');print('Timer icon opens the hours/minutes picker with correct disabled state.');await b.close()
async def main():await ui()
asyncio.run(main())

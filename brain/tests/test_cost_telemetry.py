import asyncio
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient,TestServer
from config import Config
from cost_telemetry.store import Ledger,normalize,parse_prices
from cost_telemetry.dashboard import snapshot
from cost_telemetry import tracking

PRICES='''### Standard pricing data
| gpt-test | $1 | $0.1 | $1.25 | $5 | $2 | $0.2 | $2.5 | $7.5 |
### Batch pricing data
| gpt-test | $0.5 | $0.05 | $0.625 | $2.5 | $1 | $0.1 | $1.25 | $3.75 |
### Unrelated table
| gpt-test | $99 | $99 | $99 | $99 | $99 | $99 | $99 | $99 |
'''
def response(**kwargs):
 return {'model':'gpt-test','id':'response-'+str(kwargs.pop('rid','1')),'service_tier':'default','usage':{'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':200,'cache_write_tokens':300}},**kwargs}
class CostStoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'costs.db';self.now=dt.datetime(2026,10,19,14,0,tzinfo=dt.timezone.utc).timestamp();self.s=Ledger(self.path,clock=lambda:self.now);self.s.set_prices(parse_prices(PRICES,self.now))
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def record(self,category='message_replies',data=None):
  c=self.s.begin(category,'reply','openai','gpt-test');self.s.finish(c,data if data is not None else response());return c
 def test_cache_reads_writes_not_double_counted(self):
  self.record();r=snapshot(self.s,{});self.assertAlmostEqual(r['totals']['cost_usd'],.001395);self.assertEqual(r['totals']['input_tokens'],1000)
 def test_empty_period_is_zero_and_all_categories_present(self):
  r=snapshot(self.s,{});self.assertEqual(r['totals']['calls'],0);self.assertEqual(r['totals']['unpriced_calls'],0);self.assertEqual(len(r['categories']),6)
 def test_repeated_response_not_charged_twice(self):
  self.record();self.record();self.assertEqual(snapshot(self.s,{})['totals']['calls'],1)
 def test_multi_round_reply_counts_every_model_call_once(self):
  for n in range(3):self.record(data=response(rid=n))
  self.s.turn('request',self.now,'message_replies','completed','normal',3);self.s.turn('request',self.now,'message_replies','completed','normal',3)
  r=snapshot(self.s,{})['totals'];self.assertEqual(r['calls'],3);self.assertEqual(r['requests'],1);self.assertEqual(r['model_calls'],3)
 def test_restart_recovers_pending_as_unknown_without_zero_cost_claim(self):
  c=self.s.begin('proactivity','attention','openai','gpt-test');self.s.close();self.s=Ledger(self.path,clock=lambda:self.now)
  r=snapshot(self.s,{});self.assertEqual(r['recent'][0]['status'],'interrupted');self.assertEqual(r['totals']['unpriced_calls'],1);self.assertIsNone(r['recent'][0]['cost_usd'])
 def test_no_backfill_and_start_survives_restart(self):
  started=self.s.meta('started_at');self.now+=60;self.s.close();self.s=Ledger(self.path,clock=lambda:self.now);self.assertEqual(self.s.meta('started_at'),started);self.assertEqual(snapshot(self.s,{})['totals']['calls'],0)
 def test_missing_prices_keep_tokens_and_null_cost(self):
  self.record(data=response(model='unknown'));r=snapshot(self.s,{});self.assertEqual(r['totals']['unpriced_calls'],1);self.assertEqual(r['recent'][0]['input_tokens'],1000);self.assertIsNone(r['recent'][0]['cost_usd'])
 def test_error_with_usage_still_costs_money(self):
  c=self.s.begin('memory','consolidation','openai','gpt-test');self.s.finish(c,response(),error='Malformed output');r=snapshot(self.s,{})['totals'];self.assertEqual(r['failed_calls'],1);self.assertGreater(r['cost_usd'],0)
 def test_error_without_usage_unknown(self):
  c=self.s.begin('other','reply','openai','gpt-test');self.s.finish(c,error='TimeoutError');r=snapshot(self.s,{})['totals'];self.assertEqual(r['unknown_usage_calls'],1);self.assertEqual(r['unpriced_calls'],1)
 def test_long_context_selects_long_rate(self):
  u={'input':272001,'output':10,'cached':0,'written':0};cost,_=self.s.estimate('gpt-test','default',u,'openai');self.assertAlmostEqual(cost,(272001*2+10*7.5)/1e6)
 def test_tier_prices_kept_separate(self):
  t={'input':100,'output':0,'cached':0,'written':0};a,_=self.s.estimate('gpt-test','default',t,'openai');b,_=self.s.estimate('gpt-test','batch',t,'openai');self.assertEqual(a,2*b);self.assertIsNone(self.s.estimate('gpt-test','priority',t,'openai')[0])
 def test_old_prices_fail_to_unknown(self):
  self.now+=15*86400;self.record();self.assertEqual(snapshot(self.s,{})['totals']['unpriced_calls'],1)
 def test_model_snapshot_matches_only_known_base(self):
  t={'input':1,'output':0,'cached':0,'written':0};self.assertIsNotNone(self.s.estimate('gpt-test-2026-10-01','default',t,'openai')[0]);self.assertIsNone(self.s.estimate('gpt-test-unknown','default',t,'openai')[0])
 def test_timezone_midnight(self):
  # 18:30 UTC is local midnight; no overlap in inclusive local-date filters.
  self.now=dt.datetime(2026,10,19,18,29,59,tzinfo=dt.timezone.utc).timestamp();self.record(data=response(rid='before'))
  self.now+=1;self.record(data=response(rid='after'))
  self.assertEqual(snapshot(self.s,{'period':'custom','start':'2026-10-19','end':'2026-10-19'})['totals']['calls'],1)
  self.assertEqual(snapshot(self.s,{'period':'custom','start':'2026-10-20','end':'2026-10-20'})['totals']['calls'],1)
 def test_categories_sum_to_total_and_filters(self):
  for i,c in enumerate(['message_replies','voice_replies','memory','proactivity','automations','other']):self.record(c,response(rid=i))
  r=snapshot(self.s,{});self.assertAlmostEqual(sum(c['cost_usd'] for c in r['categories']),r['totals']['cost_usd']);self.assertEqual(snapshot(self.s,{'category':'memory'})['totals']['calls'],1)
 def test_zero_model_turns_not_invented_api_calls(self):
  self.s.turn('fast',self.now,'message_replies','completed','fast',0);r=snapshot(self.s,{})['totals'];self.assertEqual(r['zero_model_replies'],1);self.assertEqual(r['calls'],0);self.assertEqual(r['cost_usd'],0)
 def test_daily_monthly_series_and_readonly(self):
  self.record();before=self.s.db.total_changes;r=snapshot(self.s,{'period':'year'});self.assertEqual(r['range']['group'],'month');self.assertEqual(len(r['series']),10);self.assertEqual(sum(p['calls'] for p in r['series']),1);self.assertEqual(before,self.s.db.total_changes)
 def test_custom_invalid_and_bounded(self):
  for p in [{'period':'bad'},{'category':"';drop table calls"},{'group':'bad'},{'period':'custom','start':'bad','end':'2026-10-01'},{'period':'custom','start':'2027-01-01','end':'2026-01-01'},{'period':'year','group':'hour'}]:
   with self.assertRaises(ValueError):snapshot(self.s,p)
 def test_no_prompt_content_stored_or_returned(self):
  self.record(data=response(messages=['PRIVATE'],choices=[{'message':{'content':'PRIVATE'}}]));self.assertNotIn('PRIVATE',json.dumps(snapshot(self.s,{})));self.assertNotIn('PRIVATE',self.path.read_bytes().decode(errors='ignore'))
 def test_recent_limit(self):
  for i in range(45):self.record(data=response(rid=i))
  self.assertEqual(len(snapshot(self.s,{})['recent']),40);self.assertEqual(snapshot(self.s,{})['totals']['calls'],45)
 def test_usage_validation(self):
  for u in [{},{'input_tokens':-1,'output_tokens':1},{'input_tokens':2,'output_tokens':1,'input_tokens_details':{'cached_tokens':3}},{'input_tokens':float('nan'),'output_tokens':1}]:self.assertIsNone(normalize(u))
  self.assertEqual(normalize({'prompt_tokens':100,'completion_tokens':10})['output'],10)
 def test_google_usage_and_thinking(self):
  self.assertEqual(normalize({'prompt_token_count':100,'candidates_token_count':10,'thoughts_token_count':30},'google')['output'],40)
 def test_date_presets(self):
  for p in ['today','yesterday','week','month','last_month','year','all']:
   r=snapshot(self.s,{'period':p});self.assertTrue(r['read_only']);self.assertLessEqual(r['range']['start'],r['range']['end'])
 def test_price_parser_does_not_reuse_unrelated_tables(self):
  r=parse_prices(PRICES,self.now);self.assertEqual(r['gpt-test|default']['short'][0],1);self.assertEqual(r['gpt-test|batch']['short'][0],.5)

class CostTrackingTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Ledger(Path(self.tmp.name)/'costs.db');self.s.set_prices(parse_prices(PRICES));tracking.configure(self.s)
 async def asyncTearDown(self):tracking.configure(None);self.s.close();self.tmp.cleanup()
 async def test_nonstream_records_once_preserves_result(self):
  r=response();result=await tracking.create(AsyncMock(return_value=r),model='gpt-test');self.assertIs(result,r);self.assertEqual(snapshot(self.s,{})['totals']['calls'],1)
 async def test_stream_usage_only_chunk(self):
  async def chunks():yield {'choices':[{'content':'hi'}]};yield response()
  stream=await tracking.create(AsyncMock(return_value=chunks()),model='gpt-test',stream=True)
  self.assertEqual(len([x async for x in stream]),2);self.assertEqual(snapshot(self.s,{})['totals']['input_tokens'],1000)
 async def test_stream_interrupted_marks_unknown_and_propagates(self):
  async def chunks():yield {'choices':[]};raise asyncio.CancelledError()
  stream=await tracking.create(AsyncMock(return_value=chunks()),model='gpt-test',stream=True)
  with self.assertRaises(asyncio.CancelledError):[x async for x in stream]
  self.assertEqual(snapshot(self.s,{})['recent'][0]['status'],'interrupted')
 async def test_db_failure_never_retries_or_breaks_reply(self):
  self.s.db.execute('pragma query_only=1');call=AsyncMock(return_value=response());r=await tracking.create(call,model='gpt-test');self.assertEqual(r['model'],'gpt-test');call.assert_awaited_once();self.assertGreater(self.s.errors,0)
 async def test_channel_attribution(self):
  self.assertEqual(tracking.category_for({'source':'telegram'}),'message_replies');self.assertEqual(tracking.category_for({'source':'ha','satellite_id':'s'}),'voice_replies');self.assertEqual(tracking.category_for({'source':'ha'}),'message_replies');self.assertEqual(tracking.category_for({'source':'ha_compat'}),'automations')
 async def test_background_override_does_not_inherit_reply_category(self):
  from output import current_output
  token=current_output.set(NS(request_id='private-id',telemetry_category='voice_replies'))
  try:await tracking.create(AsyncMock(return_value=response()),model='gpt-test',_category='memory',_subcategory='consolidation')
  finally:current_output.reset(token)
  self.assertEqual(snapshot(self.s,{})['recent'][0]['category'],'memory');self.assertNotIn('private-id',self.s.path.read_bytes().decode(errors='ignore'))
 async def test_http_auth_readonly_invalid_params(self):
  from main import create_app
  rt=NS(config=Config(daemon_token='test'),costs=self.s,start=AsyncMock(),close=AsyncMock());client=TestClient(TestServer(create_app(rt)));await client.start_server()
  try:
   self.assertEqual((await client.get('/v1/costs')).status,401)
   r=await client.get('/v1/costs',headers={'Authorization':'Bearer test'});self.assertEqual(r.status,200);self.assertEqual((await r.json())['currency'],'USD')
   self.assertEqual((await client.get('/v1/costs?period=bad',headers={'Authorization':'Bearer test'})).status,400)
   self.assertEqual((await client.post('/v1/costs',headers={'Authorization':'Bearer test'})).status,405)
  finally:await client.close()

import calendar
from db_schema import ensure_v1
import datetime as dt
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo

CATEGORIES = {'message_replies':'Message replies','voice_replies':'Voice replies',
              'proactivity':'Proactivity','memory':'Memory','automations':'Automations','other':'Other'}
SOURCE = 'https://developers.openai.com/api/docs/pricing.md'
TIERS = {'Standard':'default','Batch':'batch','Flex':'flex','Fast':'priority','Ultrafast':'ultrafast'}

def parse_prices(text, at=None):
    """Only named official text-token tables; never mistake batch rates for standard."""
    result={};tier=None
    for line in text.splitlines():
        if line.startswith('### '):
            tier=next((v for k,v in TIERS.items() if line.strip()==f'### {k} pricing data'),None)
        if not tier or not line.startswith('| '):continue
        cells=[x.strip() for x in line.strip('|').split('|')]
        if len(cells)!=9 or not cells[0].startswith('gpt-'):continue
        def value(x):
            if x=='-':return None
            return float(x.lstrip('$').replace(',',''))
        try:rates=[value(x) for x in cells[1:]]
        except ValueError:continue
        model=cells[0].split(' (')[0]
        result[model+'|'+tier]={'short':rates[:4],'long':rates[4:],
            'long_threshold':272000,'source':SOURCE,'retrieved_at':at if at is not None else time.time()}
    if not result:raise ValueError('No recognized official pricing tables')
    return result

def asdict(value):
    if isinstance(value,dict):return value
    if hasattr(value,'model_dump'):return value.model_dump(exclude_none=True)
    return vars(value) if value is not None and hasattr(value,'__dict__') else {}

def normalize(usage,provider='openai'):
    u=asdict(usage)
    if not u:return None
    if provider=='google':
        keys=('prompt_token_count','candidates_token_count')
        if not all(k in u for k in keys):return None
        raw=[u[keys[0]],u[keys[1]]+ (u.get('thoughts_token_count') or 0),u.get('cached_content_token_count') or 0,0]
    else:
        ik='input_tokens' if 'input_tokens' in u else 'prompt_tokens';ok='output_tokens' if 'output_tokens' in u else 'completion_tokens'
        if ik not in u or ok not in u:return None
        d=asdict(u.get('input_tokens_details') or u.get('prompt_tokens_details'))
        raw=[u[ik],u[ok],d.get('cached_tokens') or 0,d.get('cache_write_tokens') or 0]
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 or int(v)!=v for v in raw):return None
    i,o,c,w=map(int,raw)
    if c+w>i:return None
    return {'input':i,'output':o,'cached':c,'written':w}

COST_SCHEMA = ('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)', 'CREATE TABLE calls(id TEXT PRIMARY KEY,started REAL NOT NULL,finished REAL,\n          category TEXT NOT NULL,subcategory TEXT NOT NULL,request_ref TEXT,provider TEXT NOT NULL,\n          model TEXT NOT NULL,tier TEXT NOT NULL,response_id TEXT,status TEXT NOT NULL,\n          usage_known INTEGER NOT NULL DEFAULT 0,input_tokens INTEGER NOT NULL DEFAULT 0,\n          output_tokens INTEGER NOT NULL DEFAULT 0,cached_tokens INTEGER NOT NULL DEFAULT 0,\n          write_tokens INTEGER NOT NULL DEFAULT 0,cost_usd REAL,pricing TEXT,latency_ms REAL,error TEXT)', 'CREATE UNIQUE INDEX response_once ON calls(provider,response_id) WHERE response_id IS NOT NULL', 'CREATE INDEX calls_time ON calls(started)', 'CREATE TABLE turns(id TEXT PRIMARY KEY,started REAL NOT NULL,category TEXT NOT NULL,\n          status TEXT NOT NULL,path TEXT NOT NULL,model_calls INTEGER NOT NULL)', 'CREATE INDEX turns_time ON turns(started)')

class Ledger:
    def __init__(self,path,timezone='Asia/Kolkata',clock=time.time):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock;self.tz=ZoneInfo(timezone);self.errors=0
        self.db=sqlite3.connect(self.path,timeout=1);self.db.row_factory=sqlite3.Row;self.path.chmod(0o600)
        try:
            ensure_v1(self.db, COST_SCHEMA, 'Costs')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
        except BaseException:
            self.db.close()
            raise
        if self.meta('started_at') is None:self.set_meta('started_at',clock());self.set_meta('timezone',timezone)
        elif self.meta('timezone')!=timezone:raise ValueError('Telemetry timezone changed; explicit migration required')
        with self.db:
            self.db.execute("UPDATE calls SET status='interrupted',error='Service restarted before final usage',finished=? WHERE status='pending'",(clock(),))
        self.prices=self.meta('prices',{})
    def meta(self,key,default=None):
        r=self.db.execute('select value from meta where key=?',(key,)).fetchone();return json.loads(r[0]) if r else default
    def set_meta(self,key,value):
        with self.db:self.db.execute('insert or replace into meta values(?,?)',(key,json.dumps(value)))
    def set_prices(self,prices):self.set_meta('prices',prices);self.prices=prices
    def begin(self,category,subcategory,provider,model,tier='default',request_ref=None):
        cid=uuid.uuid4().hex
        with self.db:self.db.execute('insert into calls(id,started,category,subcategory,request_ref,provider,model,tier,status) values(?,?,?,?,?,?,?,?,?)',
            (cid,self.clock(),category if category in CATEGORIES else 'other',str(subcategory)[:80],request_ref,provider,str(model)[:150],str(tier)[:40],'pending'))
        return cid
    def estimate(self,model,tier,tokens,provider):
        if tokens is None:return None,None
        if provider=='anthropic' and model=='claude-haiku-5-5':
            # Verified official direct-API rates, 29 October 2026; fail unpriced when stale.
            verified_at=1793247276.2156575
            if self.clock()-verified_at>14*86400:return None,None
            rates=[.1,.01,.125,.5] if tokens['input']<=100000 else [.5,.05,.625,2.5]
            amounts=[tokens['input']-tokens['cached']-tokens['written'],tokens['cached'],tokens['written'],tokens['output']]
            return sum(n*p for n,p in zip(amounts,rates))/1e6,{'source':'https://platform.claude.com/docs/en/models/haiku-5-5/overview','retrieved_at':verified_at,'provider':'anthropic','cache_ttl':'5m','rates':rates}
        if provider!='openai':return None,None
        tier={'standard':'default','auto':'default','fast':'priority'}.get(tier,tier)
        rate=self.prices.get(model+'|'+tier)
        if not rate:
            base=re.sub(r'-\d{4}-\d{2}-\d{2}$','',model);rate=self.prices.get(base+'|'+tier)
        if not rate:return None,None
        # Do not silently use stale billing rates when refresh has failed for weeks.
        if self.clock()-rate['retrieved_at']>14*86400:return None,None
        rates=rate['long' if tokens['input']>rate['long_threshold'] else 'short']
        amounts=[tokens['input']-tokens['cached']-tokens['written'],tokens['cached'],tokens['written'],tokens['output']]
        if any(n and p is None for n,p in zip(amounts,rates)):return None,None
        return sum(n*(p or 0) for n,p in zip(amounts,rates))/1e6,rate
    def finish(self,cid,response=None,*,error=None,status=None):
        row=self.db.execute('select * from calls where id=?',(cid,)).fetchone()
        if not row:return
        data=asdict(response);usage=data.get('usage_metadata') if row['provider']=='google' else data.get('usage')
        t=normalize(usage,row['provider']);model=data.get('model') or data.get('model_version') or row['model'];tier=data.get('service_tier') or row['tier']
        price,rates=self.estimate(model,tier,t,row['provider']);t=t or {'input':0,'output':0,'cached':0,'written':0}
        rid=data.get('id') or data.get('response_id');end=self.clock()
        final=status or ('error' if error else data.get('status') or 'completed')
        try:
            with self.db:self.db.execute('update calls set finished=?,model=?,tier=?,response_id=?,status=?,usage_known=?,input_tokens=?,output_tokens=?,cached_tokens=?,write_tokens=?,cost_usd=?,pricing=?,latency_ms=?,error=? where id=?',
                (end,model,tier,rid,final,int(normalize(usage,row['provider']) is not None),t['input'],t['output'],t['cached'],t['written'],price,json.dumps(rates) if rates else None,max(0,(end-row['started'])*1000),str(error)[:100] if error else None,cid))
        except sqlite3.IntegrityError:
            # Duplicate provider response delivery must not add a second charge.
            with self.db:self.db.execute('delete from calls where id=?',(cid,))
    def turn(self,request_ref,started,category,status,path,model_calls):
        with self.db:self.db.execute('insert or ignore into turns values(?,?,?,?,?,?)',(request_ref,started,category,status,path,model_calls))
    def close(self):self.db.close()

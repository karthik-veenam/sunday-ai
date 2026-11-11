"""Single bounded writer, separate versioned ledger, cached projection. Simulated feelings only."""
import asyncio
import concurrent.futures
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from .contracts import config, emotion, interaction

from .engine import BASE, PROFILE, fresh, advance, apply, project
@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    ref: str
    actor: str | None = None
    inference: dict | None = None
    def checked(self):
        if self.kind not in ('interaction_completed','touch','service_completed','action_unknown','affect','interaction_classified'):raise ValueError('event kind')
        if any(not isinstance(x,str) or not 1<=len(x)<=160 for x in (self.id,self.ref)):raise ValueError('event reference')
        if self.actor is not None and (not isinstance(self.actor,str) or len(self.actor)>80):raise ValueError('actor')
        if self.kind=='affect' and not emotion(self.inference,self.ref):raise ValueError('inference')
        if self.kind=='interaction_classified' and not interaction(self.inference,self.ref):raise ValueError('classification')
        if self.kind not in ('affect','interaction_classified') and self.inference is not None:raise ValueError('inference')
        return self

class Ledger:
    def __init__(self,path,now=time.time):
        self.now=now
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=.25)
        import os
        os.chmod(path,0o600)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1):
            self.db.close();raise ValueError('unsupported life schema')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,kind TEXT NOT NULL,ref TEXT NOT NULL,actor TEXT,at REAL NOT NULL,inference TEXT);
CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_time ON events(at,id);
PRAGMA user_version=1;''')
        row=self.db.execute('SELECT data FROM state WHERE id=1').fetchone()
        self.state=json.loads(row[0]) if row else fresh(now())
        self.state=advance(self.state,max(0,min(6*3600,now()-self.state['updated_at'])),now())
        with self.db:self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(self.state,allow_nan=False),))
        # UTC restart gap is clamped; subsequent decay uses monotonic time only.
        self.mono=time.monotonic()
    def apply(self,event,capture_gap=False):
        event.checked();now=self.now();old=json.loads(json.dumps(self.state))
        try:
            with self.db:
                inserted=self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',(event.id,event.kind,event.ref,event.actor,now,json.dumps(event.inference) if event.inference else None)).rowcount
                if not inserted:return self.snapshot()
                if capture_gap:self.state['pattern_gap_at']=now
                elapsed=max(0,min(6*3600,time.monotonic()-self.mono));self.mono=time.monotonic()
                self.state=apply(self.state,event,elapsed,now)
                # Bounded history; lifetime coverage/count are not misrepresented as retained detail.
                self.db.execute('DELETE FROM events WHERE at < ?',(now-30*86400,))
                removed=self.db.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY at DESC,id DESC LIMIT -1 OFFSET 10000)').rowcount
                if removed:self.state['history_truncated']=True
                self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(self.state,allow_nan=False),))
        except Exception:
            self.state=old;raise
        return self.snapshot()
    def snapshot(self):
        data=json.loads(json.dumps(self.state))
        if data['affect'] and data['affect']['expires_at']<=self.now():data['affect']=None
        data['simulated']=True
        data['profile']=dict(PROFILE)
        data.update(project(data,self.now()))
        now=self.now()
        data['recent_windows']={}
        for hours in (72,168):
            start=now-hours*3600
            counts=dict(self.db.execute('SELECT kind,count(*) FROM events WHERE at>=? GROUP BY kind',(start,)).fetchall())
            data['recent_windows'][str(hours)+'h']={'complete_coverage':False,'capture_mode':'best_effort','full_window_age':data['coverage_since']<=start,'counts':counts,'since':max(start,data['coverage_since'])}
        return data
    def tick(self,capture_gap=False):
        now=self.now();mono=time.monotonic()
        if capture_gap:self.state['pattern_gap_at']=now
        self.state=advance(self.state,mono-self.mono,now);self.mono=mono
        with self.db:self.db.execute('INSERT OR REPLACE INTO state VALUES(1,?)',(json.dumps(self.state,allow_nan=False),))
        return self.snapshot()
    def close(self):self.db.close()

class Service:
    def __init__(self,path,flags=None):
        self.flags=flags if flags is not None else config()
        self._utility_refs={}
        self._gap_counters=(0,0)
        self.enabled=self.flags['alive_enabled'] and self.flags['emotion_memory']
        self.path=path;self.cached=None;self.queue=asyncio.Queue(maxsize=256);self.task=None;self.errors=0;self.dropped=0;self.pool=None;self.ledger=None
    async def start(self):
        if not self.enabled or self.task:return
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='alive-ledger')
        try:
            self.ledger=await asyncio.get_running_loop().run_in_executor(self.pool,Ledger,self.path)
            self.cached=await asyncio.get_running_loop().run_in_executor(self.pool,self.ledger.snapshot)
            self.task=asyncio.create_task(self._writer(),name='alive-ledger')
        except Exception:
            self.errors+=1;self.enabled=False
            if self.ledger:
                await asyncio.get_running_loop().run_in_executor(self.pool,self.ledger.close)
            self.pool.shutdown(wait=False);self.pool=None
    def submit(self,event):
        if not self.enabled or not self.task:return False
        try:event.checked();self.queue.put_nowait(event);return True
        except (ValueError,asyncio.QueueFull):self.dropped+=1;return False
    async def _writer(self):
        while True:
            gap=(self.errors,self.dropped)!=self._gap_counters
            self._gap_counters=(self.errors,self.dropped)
            try:event=await asyncio.wait_for(self.queue.get(),timeout=60)
            except asyncio.TimeoutError:
                try:self.cached=await asyncio.get_running_loop().run_in_executor(self.pool,self.ledger.tick,gap)
                except Exception:self.errors+=1
                continue
            try:self.cached=await asyncio.get_running_loop().run_in_executor(self.pool,self.ledger.apply,event,gap)
            except Exception:self.errors+=1
            finally:self.queue.task_done()
    def bind(self,output,source):
        output.alive_social=source in ('voice','telegram','ha') and not output.read_only
        output.alive_capture_enabled=output.alive_social and self.enabled
        output.alive_hints=output.alive_social and self.flags['alive_enabled'] and self.flags['brain_performance_hints'] and source=='voice'
        output.alive_capture=lambda value:output.alive_social and self.submit(Event('affect:'+output.request_id,'affect',output.request_id,output.initiator.get('id'),value))
        if not self.enabled or not self.cached:return
        data=dict(self.cached)
        data['collection_gaps']=bool(self.errors or self.dropped)
        if data['collection_gaps']:
            data['recent_windows']={key:{**value,'complete_coverage':False} for key,value in data.get('recent_windows',{}).items()}
        if data.get('affect') and data['affect']['expires_at']<=time.time():data['affect']=None
        # A compact cached projection; no SQL, discovery, blocking disk or model round.
        context={key:data.get(key) for key in ('simulated','mood','daily_mood','mood_reasons',
            'resting_expression','voice_baseline','pattern_coverage_since','collection_gaps')}
        context['relationship']={'label':data.get('relationship',{}).get('label','comfortable')}
        context['pattern_counts']=data.get('interaction_pattern',{}).get('counts',{})
        context['recent_reactions']=[{k:c[k] for k in ('kind','cause','explanation','reason_ref','at') if k in c}
                                   for c in data.get('day_mood',{}).get('causes',[])[-3:]]
        sleep=getattr(self,'sleep_cached',None)
        if sleep and 0<=time.time()-sleep.get('updated_at',0)<=90:
            context['sleep']={k:sleep.get(k) for k in ('state','sleepiness','reasons','woken_from_sleep','desired_night')}
        output.alive_context='\nSunday emotional awareness (simulated character state): '+json.dumps(context,separators=(',',':'))+"\nYour daily mood and longer-term connection are distinct. Let the supplied state subtly color face and vocal tone; current conversation and user needs take priority. If the supplied mood is not sad or subdued, do not accept a question's premise or invent sadness. If asked why you look or sound sad, explain only supplied mood_reasons in natural first person, without meters, internal references, or an invented episode. A pattern is partial observed interaction, not proof somebody does not care. Concern means empathy for the user; disappointment means wanting to correct your own mistake. No guilt, accusations, demands for attention, refusal, slower service or unsolicited emotional commentary. A cheerful moment can lift today's mood without instantly repairing a multi-day connection. Do not invent human suffering. In the same inference, private envelopes may classify this turn and interpret an affect. No extra call for feelings. Sleep is separate from mood. If the supplied sleep state says you were woken from sleep, a brief sleepy expression or soft vocal tone may fit; do not add a preamble, delay help, or obstruct urgent requests. Explain sleep/awake reasons naturally when asked, without inventing dreams or sleep history."
        if source=='voice' and getattr(output,'audio_tags_enabled',False):
            output.mood_voice_tags=list(data.get('voice_baseline') or [])

    def on_world_event(self,event):
        kind={'SUNDAY_ACTION_COMPLETED':'service_completed','SUNDAY_ACTION_UNKNOWN':'action_unknown'}.get(event.event_type)
        if kind:
            # HA service acknowledgement is not evidence of physical actuation.
            ref=event.correlation.get('request_id')
            if ref:
                self._utility_refs[ref]=True
                if len(self._utility_refs)>256:self._utility_refs.pop(next(iter(self._utility_refs)))
                self.submit(Event('world:'+event.event_id,kind,ref))

    def completed(self,output):
        if not getattr(output,'alive_social',False):return
        self.submit(Event('interaction:'+output.request_id,'interaction_completed',output.request_id,output.initiator.get('id')))
        value=getattr(output,'interaction_classification',None)
        action_evidence=self._utility_refs.pop(output.request_id,False) or getattr(output,'alive_fast_utility',False)
        if value and action_evidence:
            value=dict(value)
            if value['kind']=='social':value['kind']='mixed'
            elif value['kind']=='unknown':value.update(kind='utility',confidence=1.)
        if not value:
            kind='utility' if action_evidence else 'unknown'
            value={'kind':kind,'confidence':1. if kind=='utility' else 0.,'reason_ref':output.request_id}
        self.submit(Event('classification:'+output.request_id,'interaction_classified',output.request_id,output.initiator.get('id'),value))
    async def close(self):
        if self.task:
            try:await asyncio.wait_for(self.queue.join(),timeout=2)
            except asyncio.TimeoutError:self.dropped+=self.queue.qsize()
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
            await asyncio.get_running_loop().run_in_executor(self.pool,self.ledger.close)
            self.pool.shutdown(wait=False);self.task=None

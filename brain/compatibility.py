"""Dedicated legacy contracts. No changes to HA callers or voice protocol."""
import asyncio
from db_schema import ensure_v1
import hmac
import json
import sqlite3
import uuid
from aiohttp import web

COMPATIBILITY_SCHEMA = (
    'CREATE TABLE deliveries(id INTEGER PRIMARY KEY, channel TEXT, message TEXT, status TEXT, message_id INTEGER)',
    'CREATE TABLE requests(id TEXT PRIMARY KEY,kind TEXT,status TEXT,payload TEXT,result TEXT)',
)

class Compatibility:
    def __init__(self,runtime,path,announce=None):
        self.runtime=runtime;self.tasks=set();self.announce=announce or self.deliver
        self.db=sqlite3.connect(path)
        try:
            ensure_v1(self.db, COMPATIBILITY_SCHEMA, 'Compatibility')
            self.db.execute('PRAGMA synchronous=FULL')
            with self.db:self.db.execute("UPDATE requests SET status='unknown' WHERE status='processing'")
        except BaseException:
            self.db.close()
            raise
    async def deliver(self,message,channel):
        results={}
        if channel in ('voice','both'):
            if not self.runtime.config.outbound_speech_enabled: results['voice']='disabled'
            else:
                satellite=self.runtime.config.default_satellite
                state=await self.runtime.ha.get_state(satellite)
                results['voice']=await self.runtime.ha.call_service('assist_satellite','announce',{'entity_id':satellite,'message':message,'preannounce':False}) if state['state']=='idle' else 'busy_or_unavailable'
        if channel in ('telegram','both'):
            if not self.runtime.telegram: results['telegram']='disabled'
            else:
                # No polling here; same singleton owner's authenticated transport.
                try:
                    with self.db:
                        receipt=self.db.execute("INSERT INTO deliveries(channel,message,status) VALUES('telegram',?,'sending')",(message,)).lastrowid
                    delivered=await self.runtime.telegram.api.call('sendMessage',chat_id=self.runtime.config.telegram_chat_id,text=message[:4000])
                    with self.db:self.db.execute("UPDATE deliveries SET status='sent',message_id=? WHERE id=?",(delivered['message_id'],receipt))
                    results['telegram']='ok'
                except Exception: results['telegram']='unknown_or_failed'
        return results
    def app(self,token):
        if not token: raise ValueError('Compatibility token required')
        app=web.Application(client_max_size=32*1024)
        async def parse(request):
            try: body=await request.json()
            except Exception: raise web.HTTPBadRequest(text='bad json')
            if not isinstance(body,dict): raise web.HTTPBadRequest(text='bad json')
            supplied=body.get('token')
            if not isinstance(supplied,str) or not hmac.compare_digest(supplied,token): raise web.HTTPUnauthorized(text='bad token')
            return body
        async def announce(request):
            body=await parse(request);message=body.get('message');channel=body.get('channel') or 'voice'
            if not isinstance(message,str) or not message.strip(): raise web.HTTPBadRequest(text='empty message')
            if not isinstance(channel,str) or channel.strip().lower() not in ('voice','telegram','both'): raise web.HTTPBadRequest(text='bad channel')
            rid=uuid.uuid4().hex;payload={'message':message.strip(),'channel':channel.strip().lower()}
            with self.db:self.db.execute('INSERT INTO requests VALUES(?,?,?,?,NULL)',(rid,'announce','processing',json.dumps(payload)))
            try: result=await self.announce(**payload)
            except Exception: result={'delivery':'unknown_or_failed'}
            success=bool(result) and all(v=='ok' for v in result.values())
            with self.db:self.db.execute('UPDATE requests SET status=?,result=? WHERE id=?',('completed' if success else 'unknown',json.dumps(result),rid))
            return web.Response(text='ok' if success else 'delivery incomplete',status=200 if success else 503,headers={'X-Sunday-Receipt':rid})
        async def ask(request):
            body=await parse(request);text=body.get('text')
            if not isinstance(text,str) or not text.strip(): raise web.HTTPBadRequest(text='empty text')
            rid=uuid.uuid4().hex
            with self.db:self.db.execute('INSERT INTO requests VALUES(?,?,?,?,NULL)',(rid,'ask','accepted',json.dumps({'text':text.strip()})))
            task=asyncio.create_task(self.run_ask(rid,text.strip()));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
            return web.Response(text='ok',headers={'X-Sunday-Receipt':rid})
        app.router.add_post('/announce',announce);app.router.add_post('/ask',ask)
        return app
    async def run_ask(self,rid,text):
        with self.db:self.db.execute("UPDATE requests SET status='processing' WHERE id=?",(rid,))
        final=None
        async def emit(e):
            nonlocal final
            if e['type']=='final':final=e['text']
        try:
            async with asyncio.timeout(120):
                await self.runtime.turn({'request_id':'compat:'+rid,'conversation_id':'compat:'+rid,'source':'ha_compat','text':text},emit)
                result=await self.announce(final or 'I could not finish that request.','voice')
            status='completed' if result and all(v=='ok' for v in result.values()) else 'unknown'
        except Exception:result={'error':'unknown_or_failed'};status='unknown'
        with self.db:self.db.execute('UPDATE requests SET status=?,result=? WHERE id=?',(status,json.dumps(result),rid))
    async def resume(self):
        for rid,payload in self.db.execute("SELECT id,payload FROM requests WHERE kind='ask' AND status='accepted'").fetchall():
            task=asyncio.create_task(self.run_ask(rid,json.loads(payload)['text']));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
    async def close(self):
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.db.close()

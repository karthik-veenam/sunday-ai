"""Telegram inbox/outbox: crash-safe at-most-once attempts, shared Runtime.turn."""
import asyncio
import fcntl
import hashlib
import json
import sqlite3
import subprocess
import shlex
import time
from pathlib import Path
import aiohttp
from db_schema import ensure_v1
from text_presentation import telegram_text

class Rejected(Exception):
    def __init__(self, retry_after=None, code=None): self.retry_after=retry_after; self.code=code

class TelegramAPI:
    def __init__(self,token):
        self.url='https://api.telegram.org/bot'+token+'/'
        self.session=None
    async def call(self,method,**data):
        if self.session is None: self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        async with self.session.post(self.url+method,json=data) as r:
            body=await r.json()
            if not body.get('ok'): raise Rejected(body.get('parameters',{}).get('retry_after'),body.get('error_code'))
            return body['result']
    async def close(self):
        if self.session: await self.session.close()

class TelegramTransport:
    def __init__(self,runtime,path,api=None):
        self.runtime=runtime; self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.api=api or TelegramAPI(runtime.config.telegram_bot_token)
        self.lock=None
        self.lock_path=self.path.with_suffix('.lock') if api is not None else Path('/home/djpi/.local/state/sunday') / ('telegram-'+hashlib.sha256(runtime.config.telegram_bot_token.encode()).hexdigest()[:16]+'.lock')
        self.lock_path.parent.mkdir(parents=True,exist_ok=True)
        # Lock before opening/recovering the inbox so a second process cannot
        # change an active owner's durable processing state.
        if api is None: self.acquire()
        self.db=sqlite3.connect(self.path)
        try:
            ensure_v1(self.db, (
                'CREATE TABLE meta(key TEXT PRIMARY KEY,value INTEGER)',
                'CREATE TABLE updates(id INTEGER PRIMARY KEY,status TEXT NOT NULL,chat TEXT,text TEXT,reply TEXT,message_id INTEGER)',
            ), 'Telegram')
            self.db.execute('PRAGMA synchronous=FULL')
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO meta VALUES("offset",0)')
                # A prior process may have acted or sent a reply: do not replay.
                self.db.execute("UPDATE updates SET status='unknown' WHERE status IN ('processing','sending')")
        except BaseException:
            self.db.close()
            if self.lock: self.lock.close(); self.lock=None
            raise
        self.retry_at=0
    @property
    def offset(self): return self.db.execute('SELECT value FROM meta WHERE key="offset"').fetchone()[0]
    def allowed(self,message):
        users=self.runtime.config.telegram_users or {}
        chat=str(message.get('chat',{}).get('id')); sender=str(message.get('from',{}).get('id'))
        return message.get('chat',{}).get('type')=='private' and chat in users and sender==chat and not message.get('from',{}).get('is_bot',False)
    def claim(self,update):
        uid=update['update_id']; msg=update.get('message',{})
        status='processing' if self.allowed(msg) and isinstance(msg.get('text'),str) else 'ignored'
        with self.db:
            cur=self.db.execute('INSERT OR IGNORE INTO updates(id,status,chat,text) VALUES(?,?,?,?)',(uid,status,str(msg.get('chat',{}).get('id')),msg.get('text')))
            self.db.execute('UPDATE meta SET value=max(value,?) WHERE key="offset"',(uid+1,))
        return status if cur.rowcount else None
    async def handle(self,update):
        status=self.claim(update)
        if status!='processing': return
        uid=update['update_id']; msg=update['message']; chat=str(msg['chat']['id'])
        final=None
        async def emit(event):
            nonlocal final
            if event['type']=='final': final=event['text']
        try:
            async with asyncio.timeout(120):
                request={'protocol':1,'request_id':f'telegram:{uid}','conversation_id':f'telegram:{chat}','text':msg['text'],'source':'telegram'}
                proactive=getattr(self.runtime,'proactivity',None);reply=(msg.get('reply_to_message') or {}).get('message_id')
                if proactive and isinstance(reply,int):
                    try:
                        linked=proactive.correlate_reply(chat,reply,uid)
                        if linked:request['proactive_reply_to']=linked
                    except Exception:pass # Missing thread metadata never diverts the normal conversation.
                await self.runtime.turn(request,emit)
            if not final: raise RuntimeError('No final response')
            with self.db: self.db.execute("UPDATE updates SET status='ready',reply=? WHERE id=?",(final,uid))
            await self.send(uid)
        except asyncio.CancelledError: raise
        except Exception:
            with self.db: self.db.execute("UPDATE updates SET status='unknown' WHERE id=? AND status='processing'",(uid,))
    async def send(self,uid):
        if time.time() < self.retry_at: return
        row=self.db.execute("SELECT chat,reply FROM updates WHERE id=? AND status='ready'",(uid,)).fetchone()
        if not row: return
        with self.db: self.db.execute("UPDATE updates SET status='sending' WHERE id=?",(uid,))
        try:
            # One bounded message avoids partial multi-message replay.
            result=await self.api.call('sendMessage',chat_id=row[0],text=telegram_text(row[1])[:4000])
            with self.db: self.db.execute("UPDATE updates SET status='sent',message_id=? WHERE id=?",(result['message_id'],uid))
        except Rejected as e:
            # API explicitly rejected: safe to retry only its flood response.
            with self.db: self.db.execute('UPDATE updates SET status=? WHERE id=?',('ready' if e.retry_after else 'rejected',uid))
            if e.retry_after: self.retry_at=time.time()+max(e.retry_after,1)
        except Exception:
            with self.db: self.db.execute("UPDATE updates SET status='unknown' WHERE id=?",(uid,))
    def legacy_fenced(self):
        state=subprocess.run(['systemctl','is-active','sunday.service'],capture_output=True,text=True)
        if state.stdout.strip() in ('inactive','failed'): return True
        expected=getattr(self.runtime.config,'legacy_telegram_fence_sha256','')
        if not expected: return False
        try:
            if state.stdout.strip() == 'activating':
                # During an approved legacy restart no Python poller exists yet.
                # Trust only the pinned source AND the manager's effective fence.
                configured=subprocess.run(['systemctl','show','sunday.service','--property=Environment','--value'],capture_output=True,text=True,check=True).stdout
                return 'SUNDAY_TELEGRAM_ENABLED=0' in shlex.split(configured) and hashlib.sha256(Path('/home/djpi/sunday/main.py').read_bytes()).hexdigest()==expected
            pid=int(subprocess.run(['systemctl','show','sunday.service','--property=MainPID','--value'],capture_output=True,text=True,check=True).stdout.strip())
            # Read only the required fence variable; never log process environment.
            environment=Path(f'/proc/{pid}/environ').read_bytes().split(bytes([0]))
            return b'SUNDAY_TELEGRAM_ENABLED=0' in environment and hashlib.sha256(Path('/home/djpi/sunday/main.py').read_bytes()).hexdigest()==expected
        except (OSError,ValueError,subprocess.SubprocessError): return False
    def acquire(self):
        if not self.legacy_fenced(): raise RuntimeError('Legacy Telegram poller must be stopped or explicitly fenced before transfer')
        if self.lock: return
        self.lock=self.lock_path.open('a')
        try: fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: self.lock.close(); self.lock=None; raise RuntimeError('Telegram poller already owned')
    async def loop(self):
        if not self.lock: self.acquire()
        try:
            while True:
                # Fail closed if the legacy owner is restarted externally.
                if not await asyncio.to_thread(self.legacy_fenced): raise RuntimeError('Legacy Telegram ownership fence lost')
                for (uid,) in self.db.execute("SELECT id FROM updates WHERE status='ready' ORDER BY id").fetchall(): await self.send(uid)
                try:
                    updates=await self.api.call('getUpdates',offset=self.offset,timeout=25,allowed_updates=['message'])
                    for update in sorted(updates,key=lambda u:u['update_id']): await self.handle(update)
                except Rejected as e:
                    if e.code in (401,409): raise RuntimeError('Telegram authentication or competing poller conflict')
                    remaining=e.retry_after or 5
                    while remaining>0:
                        delay=min(remaining,60); await asyncio.sleep(delay); remaining-=delay
                except (aiohttp.ClientError,TimeoutError): await asyncio.sleep(5)
        finally:
            if self.lock: self.lock.close(); self.lock=None
    async def close(self):
        await self.api.close(); self.db.close()
        if self.lock: self.lock.close(); self.lock=None

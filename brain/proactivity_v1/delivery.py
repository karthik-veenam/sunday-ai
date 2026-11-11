"""Trusted Telegram-only communication delivery, independent of model execution.

The sole input is a stored proposal ID. Tokens/chat IDs never come from a model.
"""
import asyncio
import json
import re
import uuid
from pathlib import Path
from .policy import validate_output
from .store import digest
from telegram_transport import Rejected

COMM={'TELL_USER':'proactive_tell_enabled','ASK_USER':'proactive_ask_enabled'}
TERMINAL={'DELIVERED','SUPPRESSED','CANCELLED','UNKNOWN'}

def read_controls(path):
    try:
        data=json.loads(Path(path).read_text())
        return {k:data.get(k) is True for k in (*COMM.values(),'proactive_act_enabled','proactive_voice_enabled')}
    except Exception:return {k:False for k in (*COMM.values(),'proactive_act_enabled','proactive_voice_enabled')}

def route(presence,approved=False,requested_channel=None):
    if not approved:raise ValueError('Communication gate approval required')
    if requested_channel not in (None,'telegram'):raise ValueError('Unsupported proactive channel')
    # Presence records context only. It never confers permission or enables voice.
    return {'channel':'telegram','presence_at_route_time':presence if presence in {'HOME','OFFICE','AWAY','UNKNOWN'} else 'UNKNOWN'}

class Delivery:
    def __init__(self,store,config,gates,telegram,revalidate,*,scheduler=None,controls=None,on_delivered=None,final_check=None):
        self.store=store;self.config=config;self.gates=gates;self.telegram=telegram;self.revalidate=revalidate;self.scheduler=scheduler;self.controls_reader=controls;self.on_delivered=on_delivered;self.final_check=final_check
        self.event=asyncio.Event();self.lock=asyncio.Lock();self.task=None
        if any(self.controls().get(k) for k in COMM.values()) and not store.meta('telegram_release_generation'):
            store.set_meta('telegram_release_generation',uuid.uuid4().hex);store.set_meta('telegram_release_started_at',store.clock())
    def controls(self):
        return self.controls_reader() if self.controls_reader else {k:bool(getattr(self.config,k,False)) for k in (*COMM.values(),'proactive_act_enabled','proactive_voice_enabled')}
    def destination(self):
        chat=str(getattr(self.config,'telegram_chat_id',''));users=getattr(self.config,'telegram_users',None) or {};primary=getattr(self.telegram,'primary_person','Karthik')
        if not getattr(self.config,'telegram_enabled',False) or not re.fullmatch(r'[1-9][0-9]{3,19}',chat) or str(users.get(chat,'')).casefold()!=str(primary).casefold():raise ValueError('Trusted private primary-person destination unavailable')
        if not getattr(self.telegram,'api',None):raise ValueError('Existing Telegram transport unavailable')
        return chat
    def reject(self,reason,proposal_id=None):self.store.audit('telegram_adapter_rejected',{'reason':reason,'proposal_id':proposal_id})
    def base(self,row):
        d=row['data'];p=d.get('proposal',{});wake=d.get('wake',{});op=p.get('operation')
        if row['state']!='GATE_APPROVED':return False,'Proposal not active',None
        try:validate_output({'outcomes':[p],'reason':'Trusted delivery validation'})
        except Exception:return False,'Malformed proposal',None
        msg=p.get('message','')
        if d.get('operation')!=op or d.get('authority')!=wake.get('authority') or d.get('evidence_refs')!=wake.get('evidence_refs'):return False,'Proposal authority/evidence mismatch',None
        if op not in COMM or p.get('destination')!='telegram':return False,'Only Telegram communication accepted',None
        try:msg.encode('utf-8')
        except UnicodeError:return False,'Malformed message encoding',None
        if any(ord(c)<32 and c not in '\n\t' for c in msg):return False,'Malformed message controls',None
        flags=self.controls()
        if not flags.get(COMM[op]):return False,'Communication feature disabled',None
        generation=self.store.meta('telegram_release_generation')
        if not generation or d.get('release_generation')!=generation:return False,'Historical or non-live proposal',None
        a=self.store.get('approvals',d.get('gate_decision_id',''))
        if not a or a['state']!='APPROVED':return False,'Missing or revoked gate approval',None
        ad=a['data']
        if ad.get('expires_at',0)<=self.store.clock():return False,'Gate approval expired',None
        if ad.get('proposal_id')!=row['id'] or ad.get('release_generation')!=generation or ad.get('proposal_hash')!=digest(p) or ad.get('wake_hash')!=digest(wake) or ad.get('message_hash')!=digest(msg) or ad.get('evidence_refs')!=d.get('evidence_refs'):return False,'Approved proposal/evidence changed',None
        incident=self.store.get('incidents',wake.get('incident_id',''))
        if not incident or incident['state']!='OPEN':return False,'Incident resolved or cancelled',None
        decision=self.store.get('decisions',d.get('decision_ref',''))
        if not decision or decision['state']!='VALIDATED' or ad.get('decision_ref')!=decision['id'] or ad.get('decision_hash')!=digest(decision['data']):return False,'Decision invalid or superseded',None
        origin=self.store.db.execute('select rowid from decisions where id=?',(decision['id'],)).fetchone()[0]
        newer=self.store.db.execute("select id from decisions where rowid>? and state='VALIDATED' and json_extract(data,'$.envelope.incident_id')=? limit 1",(origin,wake.get('incident_id'))).fetchone()
        if newer:return False,'Newer incident decision superseded proposal',None
        try:chat=self.destination()
        except ValueError:return False,'Trusted destination unresolved',None
        runtime=getattr(self.telegram,'runtime',None)
        if runtime and 'telegram:'+chat in getattr(runtime,'active',{}).values():return False,'Normal Telegram conversation is active; avoid overlapping interruption',None
        return True,'Approved stored proposal is current',a
    def enqueue(self,proposal_id):
        row=self.store.get('proposals',proposal_id)
        if not row or row['data'].get('operation') not in COMM:self.reject('Missing or unsupported proposal',proposal_id);return None
        d=row['data'];p=d.get('proposal',{});incident=d.get('wake',{}).get('incident_id')
        # No conversion of historical shadow into deliveries, even if flags now allow it.
        if row['state']!='GATE_APPROVED' or d.get('release_generation')!=self.store.meta('telegram_release_generation') or not d.get('release_generation'):
            self.reject('Historical shadow or unapproved proposal is not queued',proposal_id);return None
        key=['telegram-incident',incident]
        did=self.store.put('deliveries',{'proposal_id':proposal_id,'incident_id':incident,'operation':d['operation'],'message':p.get('message'),
            'authority':d.get('authority'),'evidence_refs':d.get('evidence_refs'),'gate_decision_id':d.get('gate_decision_id'),
            'channel':'telegram','destination_ref':'configured-primary-private-chat','dedupe_key':digest(key),
            'release_generation':d.get('release_generation'),'attempts':0,'retry_count':0,'retryable':False,'lifecycle':[{'state':'PROPOSED','at':self.store.clock()}]},dedupe=key,state='PROPOSED')
        self.event.set();return did
    def transition(self,did,state,**fields):
        row=self.store.get('deliveries',did)
        data=row['data']|fields;data['lifecycle']=data.get('lifecycle',[])+[{'state':state,'at':self.store.clock()}]
        self.store.change('deliveries',did,state=state,data=data)
        if state in TERMINAL or state=='FAILED' and not data.get('retryable'):
            p=self.store.get('proposals',data['proposal_id'])
            if p:self.store.change('proposals',p['id'],state=state,data=p['data']|{'delivery_id':did,'delivery_state':state,'external_effect':state=='DELIVERED','external_outcome_unknown':state=='UNKNOWN'})
        self.store.audit('telegram_delivery_'+state.lower(),{'delivery_id':did,'proposal_id':data['proposal_id'],'incident_id':data['incident_id'],'reason':fields.get('reason'),'failure_class':fields.get('failure_class')})
    def matches_receipt(self,receipt,proposal):
        d=proposal['data'];r=receipt['data']
        return all(r.get(k)==v for k,v in {'message':d['proposal']['message'],'operation':d['operation'],'authority':d['authority'],'evidence_refs':d['evidence_refs'],'gate_decision_id':d['gate_decision_id'],'incident_id':d['wake']['incident_id'],'channel':'telegram','destination_ref':'configured-primary-private-chat'}.items())
    async def process(self,did):
        async with self.lock:
            receipt=self.store.get('deliveries',did)
            if not receipt or receipt['state'] in TERMINAL or receipt['state']=='DISPATCHING':return
            if receipt['state']=='FAILED':
                if not receipt['data'].get('retryable') or receipt['data'].get('retry_at',float('inf'))>self.store.clock():return
                if receipt['data'].get('attempts',0)>=3:self.transition(did,'FAILED',retryable=False,reason='Attempt limit reached');return
            row=self.store.get('proposals',receipt['data']['proposal_id'])
            okay,reason,approval=self.base(row) if row else (False,'Proposal missing',None)
            if not okay:self.transition(did,'CANCELLED' if 'cancelled' in reason or 'resolved' in reason else 'SUPPRESSED',reason=reason);return
            if not self.matches_receipt(receipt,row):self.transition(did,'SUPPRESSED',reason='Receipt does not match approved proposal');return
            try:
                check=await self.revalidate(row)
            except asyncio.CancelledError:raise
            except Exception as error:
                self.transition(did,'SUPPRESSED',reason='Pre-send factual revalidation unavailable',failure_class=type(error).__name__);return
            if not check.get('eligible'):
                self.transition(did,'SUPPRESSED',reason=check.get('reason','Fresh evidence no longer supports proposal'),revalidation=check);return
            valid,why=self.gates.check(row['data']['wake'],row['data']['proposal'],check.get('context',{}))
            if not valid:self.transition(did,'SUPPRESSED',reason=why,revalidation=check);return
            # Re-read every mutable approval after awaits; arrival/flag/decision can change meanwhile.
            row=self.store.get('proposals',row['id']);okay,reason,approval=self.base(row) if row else (False,'Proposal missing',None)
            receipt=self.store.get('deliveries',did)
            if not okay or not self.matches_receipt(receipt,row):self.transition(did,'SUPPRESSED',reason=reason if not okay else 'Receipt mismatch');return
            if receipt['state'] not in {'PROPOSED','GATE_APPROVED','FAILED'}:return
            if self.final_check and not self.final_check(row,check):
                self.transition(did,'SUPPRESSED',reason='Key condition changed after async revalidation');return
            routing=route(check.get('presence','UNKNOWN'),True)
            self.transition(did,'GATE_APPROVED',route=routing,revalidation=check,validated_at=self.store.clock())
            stamp=self.store.clock();data=self.store.get('deliveries',did)['data'];data|={'attempts':data.get('attempts',0)+1,'retry_count':data.get('attempts',0),'dispatch_at':stamp,'retryable':False};data['lifecycle'].append({'state':'DISPATCHING','at':stamp})
            # Commit before network I/O: no ambiguous dispatch is ever retried after a crash.
            with self.store.lock,self.store.db:
                changed=self.store.db.execute("update deliveries set state='DISPATCHING',data=?,updated=? where id=? and state='GATE_APPROVED'",(json.dumps(data,ensure_ascii=False),stamp,did)).rowcount
            if not changed:return
            try:
                result=await self.telegram.api.call('sendMessage',chat_id=self.destination(),text=data['message'],disable_web_page_preview=True)
                message_id=result.get('message_id') if isinstance(result,dict) else None
                if not isinstance(message_id,int) or isinstance(message_id,bool):self.transition(did,'UNKNOWN',failure_class='MissingMessageReceipt',reason='No definitive Telegram message receipt');return
                self.transition(did,'DELIVERED',telegram_message_id=message_id,delivered_at=self.store.clock(),reason='Telegram accepted one message',external_effect=True)
                incident=self.store.get('incidents',row['data']['wake']['incident_id'])
                if incident and incident['data'].get('schema_version')==2:self.store.change('incidents',incident['id'],state='NOTIFIED',data=incident['data']|{'notification_state':'DELIVERED','delivery_id':did,'notified_at':self.store.clock()})
                if self.on_delivered:
                    try:self.on_delivered(self.store.get('deliveries',did))
                    except Exception as error:self.store.audit('telegram_thread_link_failed',{'delivery_id':did,'error_type':type(error).__name__})
            except Rejected as error:
                # Telegram's explicit flood rejection is definitive non-delivery. 5xx/timeouts are not.
                retryable=error.code==429 and isinstance(error.retry_after,(int,float)) and 0<error.retry_after<=3600 and data['attempts']<3
                retry_at=self.store.clock()+max(float(error.retry_after or 1),2**data['attempts']) if retryable else None
                self.transition(did,'FAILED' if error.code in (400,401,403,404,429) else 'UNKNOWN',retryable=retryable,retry_at=retry_at,failure_class='TelegramRejected'+str(error.code),reason='Explicit Telegram rejection' if error.code in (400,401,403,404,429) else 'Server outcome may be ambiguous')
                if retryable and self.scheduler:self.scheduler.add(retry_at,'TELEGRAM_DELIVERY_RETRY','SYSTEM',{'delivery_id':did},['telegram-retry',did,data['attempts']])
            except asyncio.CancelledError:
                self.transition(did,'UNKNOWN',failure_class='CancelledDuringDispatch',reason='Dispatch interrupted; never blindly resend');raise
            except Exception as error:self.transition(did,'UNKNOWN',failure_class=type(error).__name__,reason='Transport outcome ambiguous; never blindly resend')
    def recover(self):
        for row in self.store.rows('deliveries',('DISPATCHING',),1000):self.transition(row['id'],'UNKNOWN',failure_class='RestartDuringDispatch',reason='Prior dispatch outcome unknown; not replayed')
        for row in self.store.rows('deliveries',('FAILED',),1000):
            d=row['data']
            if d.get('retryable') and d.get('attempts',0)<3 and self.scheduler:self.scheduler.add(d['retry_at'],'TELEGRAM_DELIVERY_RETRY','SYSTEM',{'delivery_id':row['id']},['telegram-retry',row['id'],d['attempts']])
        for row in self.store.rows('deliveries',('DELIVERED',),1000):
            if self.on_delivered and not row['data'].get('conversation_linked'):
                self.on_delivered(row)
        # Only already-live, approved proposals of this generation may repair an interrupted enqueue.
        for row in self.store.rows('proposals',('GATE_APPROVED',),1000):self.enqueue(row['id'])
    async def worker(self):
        while True:
            self.event.clear()
            try:
                for row in self.store.rows('deliveries',('PROPOSED','GATE_APPROVED'),50):await self.process(row['id'])
                if self.store.rows('deliveries',('PROPOSED','GATE_APPROVED'),1):continue
            except asyncio.CancelledError:raise
            except Exception as error:
                self.last_error=type(error).__name__
                await asyncio.sleep(2);self.event.set();continue
            await self.event.wait()
    def start(self):
        self.recover();self.task=asyncio.create_task(self.worker(),name='proactive-telegram-delivery')
    async def close(self):
        if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)

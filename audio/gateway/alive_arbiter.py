"""Trusted transport leases, no emotion engine and no audio/model work."""
import secrets
import time
from alive_contracts import config, speech_context

class Arbiter:
    def __init__(self,flags=None,clock=time.monotonic):
        self.flags=flags if flags is not None else config()
        self.enabled=self.flags['alive_enabled'] and self.flags['brain_performance_hints']
        self.clock=clock;self.epoch=secrets.randbits(31)+1;self.generation=0
        self.idle=None;self.idle_expiry=0;self.sleep_expression=None
        self.reply=None;self.manual=False;self.activity='idle';self.expiry=0;self.pending=set();self.ended=False;self.failed=False;self.hint=None
    def token(self):return (self.epoch,self.generation)
    def valid(self,token):return token==self.token()
    def reset(self):
        self.generation+=1;self.reply=None;self.pending.clear();self.expiry=0;self.hint=None;self.ended=False;self.failed=False
    def activity_change(self,name):
        if name in ('wake','listening','thinking') and name!=self.activity:self.reset()
        self.activity=name
        return self.token()
    def manual_change(self,value):
        self.reset();self.manual=str(value).lower()!='auto'
        return self.token()
    def begin(self,context,pid):
        context=speech_context(context)
        if not self.enabled or not context:return None
        request=context['request_id']
        if request!=self.reply:
            self.reset();self.reply=request
            self.hint=context.get('performance')
        # The first clip fixes the expression for the whole reply (late hints ignored).
        self.pending.add(pid);self.expiry=self.clock()+120
        return self.token()
    def ack(self,token,pid,ok):
        if not self.valid(token):return False
        self.pending.discard(pid)
        if not ok:self.failed=True;self.hint=None;self.expiry=0
        if self.ended and not self.pending:self._afterglow()
        return True
    def end(self,request,cancelled=False):
        if request!=self.reply:return False
        self.ended=True
        if cancelled:self.failed=True;self.hint=None;self.expiry=0
        if not self.pending:self._afterglow()
        return True
    def _afterglow(self):
        self.expiry=self.clock()+((self.hint or {}).get('afterglow_ms',0)/1000 if not self.failed else 0)
    def message(self):
        active=self.enabled and not self.manual and self.hint and self.expiry>self.clock()
        message={'type':'alive','epoch':self.epoch,'seq':self.generation,'enabled':int(self.enabled),
                'expression':self.hint['expression'] if active else '',
                'ttl_ms':max(0,min(120000,int((self.expiry-self.clock())*1000))) if active else 0}

        idle=self.idle if self.enabled and self.idle_expiry>self.clock() else None
        message.update(idle_expression=(self.sleep_expression or idle['expression']) if idle else '',
            idle_ttl_ms=max(0,min(45000,int((self.idle_expiry-self.clock())*1000))) if idle else 0,
            idle_flags=idle['flags'] if idle else 0,
            idle_mood=idle['mood'] if idle else 'comfortable')
        return message

    def set_idle(self, snapshot, now):
        from alive_contracts import EXPRESSIONS
        # Only the local cache's typed projection can supply a baseline lease.
        if (not isinstance(snapshot,dict) or snapshot.get('emotion_version')!=2 or
            not isinstance(snapshot.get('resting_expression'),str) or snapshot.get('resting_expression') not in EXPRESSIONS or
            not isinstance(snapshot.get('updated_at'),(int,float)) or
            not 0<=now-snapshot['updated_at']<=150):
            self.idle=None;self.idle_expiry=0;return False
        flags=(1 if self.flags.get('idle_expressions') else 0) | (2 if self.flags.get('idle_motion') else 0) | (4 if self.flags.get('idle_sounds') else 0)
        self.idle={'expression':snapshot['resting_expression'],'flags':flags,
                   'mood':snapshot['mood'] if snapshot.get('mood') in ('comfortable','bright','concerned','disappointed','slightly_lonely') else 'comfortable',
                   'snapshot_at':snapshot['updated_at']}
        self.idle_expiry=self.clock()+45
        return True

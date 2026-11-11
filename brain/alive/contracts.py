import json
import math
import os
from pathlib import Path

EXPRESSIONS = frozenset(('Idle','Happy','Grin','Laughing','Thinking','Listening','Speaking','Surprised','Sleepy','Sleeping','Delight','Bliss','Sad'))
# Only existing firmware presets are accepted; confirmed against expressionFromName.
DEFAULTS = {'alive_enabled':False,'emotion_memory':False,'brain_performance_hints':False,
            'idle_expressions':False,'idle_motion':False,'idle_sounds':False,'sleep_cycle':False}

def config(path=None):
    result = dict(DEFAULTS)
    try:
        data = json.loads(Path(path or os.environ.get('SUNDAY_ALIVE_CONFIG','/home/djpi/sunday-alive/config.json')).read_text())
        if not isinstance(data,dict):return result
        for key in result: result[key] = data.get(key) is True
    except (OSError, ValueError, TypeError): pass
    # These are deliberately unavailable in this phase, even if misconfigured.
    # Sleep policy is now available when explicitly enabled.
    return result

def performance(value):
    if not isinstance(value,dict) or set(value)-{'expression','scope','afterglow_ms'}:return None
    if not isinstance(value.get('expression'),str) or value.get('expression') not in EXPRESSIONS or value.get('scope','reply')!='reply':return None
    delay=value.get('afterglow_ms',0)
    if isinstance(delay,bool) or not isinstance(delay,int) or not 0<=delay<=5000:return None
    return {'expression':value['expression'],'scope':'reply','afterglow_ms':delay}

def emotion(value,request_id):
    if not isinstance(value,dict) or set(value)-{'affect','strength','confidence','reason_ref','cause','explanation'}:return None
    if value.get('affect') not in ('warmth','amusement','concern','curiosity','satisfaction','disappointment'):return None
    if value.get('reason_ref') != request_id:return None
    for key in ('strength','confidence'):
        n=value.get(key)
        if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or not 0<=n<=1:return None
    if not isinstance(value.get('cause','unclear'),str) or value.get('cause','unclear') not in CAUSES:return None
    explanation=value.get('explanation','')
    if not isinstance(explanation,str) or len(explanation)>240:return None
    return dict(value)

CAUSES=frozenset(('appreciation','shared_humour','user_difficulty','own_mistake','friendly_chat','helpful_outcome','unclear'))

def interaction(value, request_id):
    if not isinstance(value,dict) or set(value)!={'kind','confidence','reason_ref'}:return None
    if value.get('kind') not in ('social','utility','mixed','unknown') or value.get('reason_ref')!=request_id:return None
    n=value.get('confidence')
    if isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) or not 0<=n<=1:return None
    return dict(value)

def speech_context(value):
    if not isinstance(value,dict):return None
    if any(not isinstance(value.get(k),str) or not 1<=len(value[k])<=128 for k in ('request_id','utterance_id')):return None
    result={k:value[k] for k in ('request_id','utterance_id')}
    hint=performance(value.get('performance'))
    if hint:result['performance']=hint
    return result

DEFINITION={'name':'express_reply','description':
    'Private optional final reply envelope, never a device action. Supply the actual concise reply and optionally an existing expression. Use in the SAME inference as the final reply, without an extra call just for a face. Do not claim an action until its result. Plain prose is always allowed.',
    'input_schema':{'type':'object','properties':{'reply':{'type':'string','maxLength':8000},
        'performance':{'type':'object','properties':{'expression':{'type':'string','enum':sorted(EXPRESSIONS)},'scope':{'type':'string','enum':['reply']},'afterglow_ms':{'type':'integer','minimum':0,'maximum':5000}},'required':['expression'],'additionalProperties':False},
        'emotion':{'type':'object','properties':{'affect':{'enum':['warmth','amusement','concern','curiosity','satisfaction','disappointment']},'strength':{'type':'number','minimum':0,'maximum':1},'confidence':{'type':'number','minimum':0,'maximum':1},'reason_ref':{'type':'string'}},'required':['affect','strength','confidence','reason_ref'],'additionalProperties':False}},'required':['reply'],'additionalProperties':False}}

# A semantic model decision in the same inference, independent of punctuation.
DEFINITION['input_schema']['properties']['continue_conversation']={
    'type':'boolean','description':'True when waiting for the user to speak next, even if the reply is a statement. False when the answer is complete. No extra model call.'}

from voice_delivery import SCHEMA as VOICE_DELIVERY_SCHEMA
DEFINITION['input_schema']['properties']['voice_delivery']=VOICE_DELIVERY_SCHEMA

DEFINITION['input_schema']['properties']['emotion']['properties']['cause']={'type':'string','enum':sorted(CAUSES),'description':'Grounded cause of the current reaction. Concern is empathy for user difficulty; disappointment only for your own mistake or explicit user frustration about your help.'}
DEFINITION['input_schema']['properties']['interaction']={'type':'object','properties':{'kind':{'type':'string','enum':['social','utility','mixed','unknown']},'confidence':{'type':'number','minimum':0,'maximum':1},'reason_ref':{'type':'string'}},'required':['kind','confidence','reason_ref'],'additionalProperties':False,'description':'Interpret ONLY this user turn. Social means personal conversation, affection, sharing or banter; utility means instrumental tasks/questions including home control; mixed contains both. Do not classify device commands as social merely because polite. No additional inference.'}

DEFINITION['input_schema']['properties']['emotion']['properties']['explanation']={'type':'string','maxLength':240,'description':'Brief evidence-grounded reason for your current reaction, especially the actual mistake or user correction. This supports explaining your mood later. No invented history, blame or internal IDs.'}

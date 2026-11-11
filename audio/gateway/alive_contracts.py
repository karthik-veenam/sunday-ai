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
    if not isinstance(value,dict) or set(value)-{'affect','strength','confidence','reason_ref','cause'}:return None
    if value.get('affect') not in ('warmth','amusement','concern','curiosity','satisfaction','disappointment'):return None
    if value.get('reason_ref') != request_id:return None
    for key in ('strength','confidence'):
        n=value.get(key)
        if isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or not 0<=n<=1:return None
    if not isinstance(value.get('cause','unclear'),str) or value.get('cause','unclear') not in CAUSES:return None
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


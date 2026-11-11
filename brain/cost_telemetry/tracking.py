"""Fail-open accounting around provider calls; telemetry never retries inference."""
import asyncio
import hashlib
import time
from .store import asdict

_ledger=None

def configure(ledger):
    global _ledger
    _ledger=ledger

def safe(method,*args,**kwargs):
    if _ledger is None:return None
    try:return getattr(_ledger,method)(*args,**kwargs)
    except Exception:
        _ledger.errors+=1
        return None

def request_ref(value):return hashlib.sha256(str(value).encode()).hexdigest()[:32]

def category_for(request):
    source=request.get('source','')
    if source=='telegram':return 'message_replies'
    if source=='ha_compat':return 'automations'
    if request.get('satellite_id') or source in ('voice','lva'):return 'voice_replies'
    return 'message_replies'

def begin(provider,model,tier='default',category=None,subcategory='reply'):
    from output import current_output
    out=current_output.get();category=category or getattr(out,'telemetry_category','other')
    ref=request_ref(out.request_id) if out else None
    return safe('begin',category,subcategory,provider,model,tier,ref)

def finish(cid,response=None,**kwargs):
    if cid:safe('finish',cid,response,**kwargs)

class TrackedStream:
    def __init__(self,stream,cid,provider):self.stream=stream;self.cid=cid;self.provider=provider
    def __aiter__(self):return self.iterate()
    async def iterate(self):
        last=None;failed=False
        try:
            async for chunk in self.stream:
                data=asdict(chunk)
                if data.get('usage') or data.get('usage_metadata'):last=chunk
                yield chunk
        except BaseException as error:
            failed=True;finish(self.cid,last,error=type(error).__name__,status='interrupted' if isinstance(error,asyncio.CancelledError) else 'error');raise
        finally:
            if not failed:finish(self.cid,last,status='completed' if last else 'usage_unavailable')
    async def close(self):
        close=getattr(self.stream,'close',None) or getattr(self.stream,'aclose',None)
        if close:await close()

async def create(call,*,_category=None,_subcategory='reply',_provider='openai',**kwargs):
    if kwargs.get('model','').startswith('claude-'):_provider='anthropic'
    cid=begin(_provider,kwargs.get('model','unknown'),('default' if _provider=='anthropic' else kwargs.get('service_tier','default')),_category,_subcategory)
    try:
        result=await call(**kwargs)
    except BaseException as error:
        finish(cid,error=type(error).__name__,status='interrupted' if isinstance(error,asyncio.CancelledError) else 'error');raise
    if kwargs.get('stream'):return TrackedStream(result,cid,_provider)
    finish(cid,result);return result

async def google_stream(call,**kwargs):
    cid=begin('google',kwargs.get('model','unknown'))
    try:return TrackedStream(await call(**kwargs),cid,'google')
    except BaseException as error:
        finish(cid,error=type(error).__name__);raise

async def response_json(session,url,*,category,subcategory,**kwargs):
    body=kwargs.get('json') or {};cid=begin('openai',body.get('model','unknown'),body.get('service_tier','default'),category,subcategory)
    try:
        async with session.post(url,**kwargs) as res:
            data=await res.json();status=res.status
        finish(cid,data,error=('HTTP '+str(status)) if status!=200 else None)
        return status,data
    except BaseException as error:
        finish(cid,error=type(error).__name__,status='interrupted' if isinstance(error,asyncio.CancelledError) else 'error');raise

async def refresh_prices(ledger):
    import aiohttp
    from .store import SOURCE,parse_prices
    while True:
        try:
            age=time.time()-max((r['retrieved_at'] for r in ledger.prices.values()),default=0)
            if age>86400:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(SOURCE) as res:
                        res.raise_for_status();text=await res.text()
                ledger.set_prices(parse_prices(text))
        except asyncio.CancelledError:raise
        except Exception:ledger.errors+=1
        await asyncio.sleep(3600)

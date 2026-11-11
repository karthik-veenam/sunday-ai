"""Bounded read-only aggregation. Dates and bucket boundaries use household time."""
import calendar
import datetime as dt
from .store import CATEGORIES

SUMS='''count(*) calls, coalesce(sum(cost_usd),0) cost_usd,
 coalesce(sum(case when cost_usd is null then 1 else 0 end),0) unpriced_calls,
 coalesce(sum(case when usage_known=0 then 1 else 0 end),0) unknown_usage_calls,
 coalesce(sum(case when status in ('error','interrupted','failed','incomplete') then 1 else 0 end),0) failed_calls,
 coalesce(sum(input_tokens),0) input_tokens,coalesce(sum(output_tokens),0) output_tokens,
 coalesce(sum(cached_tokens),0) cached_tokens,coalesce(sum(write_tokens),0) write_tokens'''

def bounds(ledger,params):
    today=dt.datetime.fromtimestamp(ledger.clock(),ledger.tz).date();preset=params.get('period','month')
    if preset=='today':start=end=today
    elif preset=='yesterday':start=end=today-dt.timedelta(days=1)
    elif preset=='week':start=today-dt.timedelta(days=6);end=today
    elif preset=='month':start=today.replace(day=1);end=today
    elif preset=='last_month':
        end=today.replace(day=1)-dt.timedelta(days=1);start=end.replace(day=1)
    elif preset=='year':start=today.replace(month=1,day=1);end=today
    elif preset=='all':start=dt.datetime.fromtimestamp(ledger.meta('started_at'),ledger.tz).date();end=today
    elif preset=='custom':
        try:start=dt.date.fromisoformat(params.get('start',''));end=dt.date.fromisoformat(params.get('end',''))
        except (ValueError,TypeError):raise ValueError('Choose valid start and end dates')
    else:raise ValueError('Unknown time period')
    if end<start or (end-start).days>366*5:raise ValueError('Date range must be ordered and no longer than five years')
    grain=params.get('group','auto')
    if grain=='auto':grain='hour' if start==end else 'month' if (end-start).days>120 else 'day'
    if grain not in ('hour','day','month'):raise ValueError('Unknown chart grouping')
    if grain=='hour' and (end-start).days>7:raise ValueError('Hourly charts support at most eight days')
    return start,end,grain

def snapshot(ledger,params):
    if ledger is None:return {'enabled':False}
    start,end,grain=bounds(ledger,params)
    cat=params.get('category','all')
    if cat!='all' and cat not in CATEGORIES:raise ValueError('Unknown category')
    begin=dt.datetime.combine(start,dt.time.min,ledger.tz).timestamp();until=dt.datetime.combine(end+dt.timedelta(days=1),dt.time.min,ledger.tz).timestamp()
    condition='started>=? and started<?';args=[begin,until]
    if cat!='all':condition+=' and category=?';args.append(cat)
    def query(sql):return [dict(x) for x in ledger.db.execute(sql,args)]
    totals=query('select '+SUMS+' from calls where '+condition)[0]
    cats={x['category']:x for x in query('select category,'+SUMS+' from calls where '+condition+' group by category')}
    categories=[dict(category=k,label=v,**{n:cats.get(k,{}).get(n,0) for n in totals}) for k,v in CATEGORIES.items()]
    models=query('select provider,model,tier,'+SUMS+' from calls where '+condition+' group by provider,model,tier order by cost_usd desc')
    subs=query('select category,subcategory,'+SUMS+' from calls where '+condition+' group by category,subcategory order by cost_usd desc')
    # Group at SQL level with household-local buckets; no raw payloads or row dump.
    fmt={'hour':'%Y-%m-%d %H:00','day':'%Y-%m-%d','month':'%Y-%m'}[grain]
    ledger.db.create_function('local_bucket',1,lambda epoch:dt.datetime.fromtimestamp(epoch,ledger.tz).strftime(fmt))
    rows=query('select local_bucket(started) bucket,category,'+SUMS+' from calls where '+condition+' group by bucket,category order by bucket')
    points={};cursor=dt.datetime.combine(start,dt.time.min,ledger.tz);limit=dt.datetime.combine(end+dt.timedelta(days=1),dt.time.min,ledger.tz)
    if grain=='month':cursor=cursor.replace(day=1)
    while cursor<limit:
        label=cursor.strftime(fmt);points[label]={'bucket':label,'cost_usd':0,'calls':0,'unpriced_calls':0,'categories':{k:0 for k in CATEGORIES}}
        if grain=='month':cursor=cursor.replace(year=cursor.year+(cursor.month==12),month=cursor.month%12+1)
        else:cursor+=dt.timedelta(hours=1) if grain=='hour' else dt.timedelta(days=1)
    for row in rows:
        p=points.get(row['bucket'])
        if p:
            for k in ('cost_usd','calls','unpriced_calls'):p[k]+=row[k] or 0
            p['categories'][row['category']]=row['cost_usd']
    turns=query("select count(*) requests,coalesce(sum(model_calls),0) model_calls,coalesce(sum(case when model_calls=0 and status='completed' then 1 else 0 end),0) zero_model_replies from turns where "+condition)[0]
    recent=query('select started,category,subcategory,provider,model,tier,status,input_tokens,output_tokens,cached_tokens,write_tokens,cost_usd,usage_known,latency_ms from calls where '+condition+' order by started desc limit 40')
    for row in recent:row['at']=dt.datetime.fromtimestamp(row.pop('started'),ledger.tz).isoformat()
    stamp=ledger.meta('started_at');now=ledger.clock()
    return {'enabled':True,'currency':'USD','timezone':str(ledger.tz),'telemetry_started_at':dt.datetime.fromtimestamp(stamp,ledger.tz).isoformat(),
        'generated_at':dt.datetime.fromtimestamp(now,ledger.tz).isoformat(),'range':{'start':str(start),'end':str(end),'group':grain,'category':cat,'partial':begin<stamp},
        'totals':totals|turns,'categories':categories,'models':models,'subcategories':subs,'series':list(points.values()),'recent':recent,
        'telemetry_errors_since_restart':ledger.errors,'pricing_source':'https://developers.openai.com/api/docs/pricing',
        'coverage':'Sunday model API estimates. Includes all model rounds per reply and rejected model outputs. Excludes HA speech recognition/synthesis, external search fees, local electricity and other applications. Memory capture shared with a reply is counted once under that reply; separate consolidation is Memory. Unknown costs are excluded from the estimate and flagged.',
        'read_only':True}

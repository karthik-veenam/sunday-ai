"""Pure deterministic analytics. Missing evidence never becomes OFF or a zero."""
import math,statistics
from datetime import datetime,timezone
from history_time import HistoryError
UNKNOWN={'UNKNOWN','unknown','unavailable','none','','None',None}

def timestamp(value):
 try:return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
 except (ValueError,AttributeError,TypeError):return None

def normalize(rows,start,end,prop='state',presence_map=None):
 samples={};bad=0;units=set()
 for row in rows:
  at=timestamp(row.get('last_updated') or row.get('last_changed') or row.get('time'))
  if at is None or at>end:bad+=1;continue
  attrs=row.get('attributes') or {};value=row.get('state') if prop=='state' else attrs.get(prop)
  if presence_map:value=presence_map(value)
  unit=attrs.get('unit_of_measurement') if prop=='state' else attrs.get('temperature_unit') if 'temperature' in prop else None
  if unit:units.add(unit)
  context=row.get('context') or {}
  samples[at]={'time':datetime.fromtimestamp(at,timezone.utc).isoformat(),'timestamp':at,'value':value,'unit':unit,'context':{k:context.get(k) for k in ('id','parent_id','user_id') if context.get(k)}}
 unique_count=len(samples)
 samples=sorted(samples.values(),key=lambda x:x['timestamp'])
 prior=[s for s in samples if s['timestamp']<=start];body=[s for s in samples if start<s['timestamp']<=end]
 samples=prior[-1:]+body
 return samples,{'malformed_rows':bad,'units':sorted(units),'duplicates_removed':max(0,len(rows)-bad-unique_count)}

def coverage(samples,start,end,predicate=None):
 points=[{'timestamp':start,'value':None}]
 if samples and samples[0]['timestamp']<=start:points=[{**samples[0],'timestamp':start}];tail=samples[1:]
 else:tail=samples
 points+=tail
 known=unknown=0.;segments=[];gaps=[]
 for i,point in enumerate(points):
  a=max(start,point['timestamp']);b=min(end,points[i+1]['timestamp'] if i+1<len(points) else end)
  if b<=a:continue
  value=point['value'];valid=value not in UNKNOWN if not isinstance(value,(dict,list)) else False
  result=predicate(value) if predicate and valid else valid
  if result is None or not valid:unknown+=b-a;gaps.append({'start':a,'end':b})
  else:known+=b-a;segments.append({'start':a,'end':b,'value':value,'matches':bool(result)})
 return {'known_seconds':known,'unknown_seconds':unknown,'available_fraction':known/(end-start) if end>start else 0,'gaps':gaps[:20],'initial_state_known':bool(samples and samples[0]['timestamp']<=start)},segments

def condition(domain,requested='on'):
 def test(value):
  if value in UNKNOWN:return None
  if requested not in ('on','off'):return str(value).lower()==str(requested).lower()
  if domain=='automation':raise HistoryError('UNSUPPORTED_METRIC','Automation ON means enabled, not execution runtime.')
  if domain=='climate':
   active={'cool','heat','heat_cool','dry','fan_only','auto'}
   if value not in active|{'off'}:return None
   return (value!='off')==(requested=='on')
  if domain=='media_player':
   active={'on','idle','playing','paused','buffering'};inactive={'off','standby'}
   if value not in active|inactive:return None
   return (value in active)==(requested=='on')
  if value not in ('on','off'):return None
  return value==requested
 return test

def durations(samples,start,end,domain,state='on'):
 cov,segments=coverage(samples,start,end,condition(domain,state));matching=[s for s in segments if s['matches']]
 return {'seconds':sum(s['end']-s['start'] for s in matching),'intervals':matching[:40],'interval_count':len(matching),'coverage':cov,'semantics':'configured active HVAC mode, not compressor runtime' if domain=='climate' else 'HA reported state intervals; not physical actuation proof'}

def transitions(samples,start,end,to_state=None,from_state=None):
 out=[];previous=None
 for sample in samples:
  value=sample['value']
  if previous is not None and start<=sample['timestamp']<end and value!=previous['value'] and value not in UNKNOWN and previous['value'] not in UNKNOWN:
   if (to_state is None or str(value)==str(to_state)) and (from_state is None or str(previous['value'])==str(from_state)):
    out.append({'time':sample['time'],'old':previous['value'],'new':value,'context':sample['context']})
  previous=sample
 return out

def numeric(samples,start,end,unit=None,state_class=None):
 clean=[];invalid=0
 for sample in samples:
  try:
   value=float(sample['value'])
   if not math.isfinite(value):raise ValueError()
   clean.append({**sample,'value':value})
  except (ValueError,TypeError):invalid+=1
 if not clean:return {'status':'NO_DATA','sample_count':0,'invalid_samples':invalid,'trend':'INSUFFICIENT_DATA'}
 values=[s['value'] for s in clean];mean=statistics.mean(values);first,last=clean[0],clean[-1];low=min(clean,key=lambda s:s['value']);high=max(clean,key=lambda s:s['value'])
 out={'minimum':{'value':low['value'],'at':low['time']},'maximum':{'value':high['value'],'at':high['time']},'mean':mean,'median':statistics.median(values),'first':{'value':first['value'],'at':first['time']},'last':{'value':last['value'],'at':last['time']},'absolute_change':last['value']-first['value'],'range':max(values)-min(values),'sample_count':len(clean),'invalid_samples':invalid,'mean_basis':'arithmetic mean of recorded numeric states; not an interpolated continuous average'}
 if unit not in ('°C','°F','C','F') and first['value']>0:out['percentage_change']=100*(last['value']-first['value'])/abs(first['value'])
 if state_class in ('total','total_increasing'):
  delta=0;resets=0
  for a,b in zip(clean,clean[1:]):
   difference=b['value']-a['value']
   if difference<0:
    resets+=1
    if state_class=='total_increasing':delta+=max(0,b['value'])
   else:delta+=difference
  out.update(counter_delta=None if state_class=='total' and resets else delta,counter_resets_observed=resets,counter_delta_semantics='observed deltas, reset-adjusted for total_increasing; reset baseline may be incomplete',trend='INSUFFICIENT_DATA',trend_reason='Cumulative series is not an instantaneous-sensor trend')
  return out
 times=[(s['timestamp']-start)/3600 for s in clean];xm=statistics.mean(times);den=sum((x-xm)**2 for x in times);slope=sum((x-xm)*(v-mean) for x,v in zip(times,values))/den if den else 0
 predicted=[mean+slope*(x-xm) for x in times];ss=sum((v-mean)**2 for v in values);residual=sum((v-p)**2 for v,p in zip(values,predicted));r2=max(0,1-residual/ss) if ss else 1
 tolerance=.2 if unit in ('°C','°F','C','F') else max(.01,abs(mean)*.01);span=times[-1]-times[0];net=last['value']-first['value'];trend='INSUFFICIENT_DATA'
 if len(clean)>=3 and span>=min((end-start)/3600*.5,1):
  if max(values)-min(values)<=tolerance:trend='ROUGHLY_STABLE'
  elif abs(net)>tolerance and abs(slope*span)>tolerance and r2>=.65 and net*slope>0:trend='RISING' if slope>0 else 'FALLING'
  else:trend='MIXED'
 out.update(trend=trend,slope_per_hour=slope,r_squared=r2,variance=statistics.pvariance(values),change_tolerance=tolerance)
 return out

def buckets(samples,start,end,max_points=48):
 if len(samples)<=max_points:
  points=[]
  for sample in samples:
   try:value=float(sample['value']);value=value if math.isfinite(value) else None
   except (ValueError,TypeError):value=None
   points.append({'time':sample['time'],'value':value})
  return points
 width=(end-start)/max_points;groups={}
 for sample in samples:
  try:value=float(sample['value'])
  except (ValueError,TypeError):continue
  if not math.isfinite(value):continue
  index=min(max_points-1,max(0,int((sample['timestamp']-start)/width)));groups.setdefault(index,[]).append(value)
 return [{'time':datetime.fromtimestamp(start+i*width,timezone.utc).isoformat(),'minimum':min(v),'maximum':max(v),'mean':statistics.mean(v),'sample_count':len(v)} for i,v in sorted(groups.items())]

import io,json,wave,threading,datetime,functools,math
from pathlib import Path
import numpy as np
from flask import Blueprint,jsonify,request,abort,send_file
B=Path('/home/djpi/sunday-openwakeword-colab/full-suite-v1');S=B/'false-positive-reviews.json';lock=threading.Lock();bp=Blueprint('false_positives',__name__)
D=json.loads((B/'negative-results.json').read_text());P=json.loads((B/'positive-results.json').read_text());FILES=D['files'];TH=[.5,.7,.8,.9,.95,.98,.99,.995,.999,.9999,.99999]
if not S.exists():S.write_text(json.dumps({'model_sha256':P['model_sha256'],'reviews':{}},indent=2))
def state():return json.loads(S.read_text())
@functools.lru_cache(maxsize=200)
def scores(index):
 p=Path(FILES[index]['file']);return np.load(B/('negative-'+p.parent.name+'-'+p.stem+'-scores.npy'))
def params():
 try:t=float(request.args.get('threshold','.5'))
 except ValueError:abort(400)
 if t not in TH:abort(400)
 return t

def category(row):
 if row['known_parent_excerpt']:return 'excerpts'
 if row['snippet_context_padding']:return 'snippets'
 if '/recordings/family_' in row['file']:return 'provisional'
 return 'continuous'

def event(index,t,tm):
 row=FILES[index];a=scores(index);frame=round(tm/.08)-1
 if not 0<=frame<len(a):abort(400)
 prefix=1. if row['snippet_context_padding'] else 0.
 duration=row['seconds']+2*prefix;start=max(0,tm-2);end=min(duration,tm+1)
 eid=f"{row['pcm_sha256'][:16]}-{frame}"
 return {'id':eid,'file_index':index,'file':Path(row['file']).name,'source':row['file'],'parent_pcm_sha256':row['pcm_sha256'],'threshold':t,'trigger_s':tm,'source_trigger_s':tm-prefix,'test_padding_s':prefix,'trigger_in_added_padding':bool(prefix and not prefix<=tm<=prefix+row['seconds']),'frame':frame,'score_at_trigger':float(a[frame]),'nearby_peak':float(a[max(0,frame-6):min(len(a),frame+13)].max()),'context_start_s':start,'context_end_s':end,'trigger_in_clip_s':tm-start,'category':category(row),'exposure':row['exposure'],'startup':tm<2.5,'label':'pending','note':''}

def find_event(eid,t):
 for index,row in enumerate(FILES):
  if not eid.startswith(row['pcm_sha256'][:16]+'-'):continue
  for tm in sorted(set(row['thresholds'][str(t)]['times_s']+row['steady_thresholds'][str(t)]['times_s'])):
   if eid==f"{row['pcm_sha256'][:16]}-{round(tm/.08)-1}":return event(index,t,tm)
 abort(404)

@bp.get('/<key>/false-positives/')
def index(key):return (Path(__file__).parent/'false-positives.html').read_text()
@bp.get('/<key>/false-positives/api')
def api(key):
 t=params();scope=request.args.get('scope','continuous');startup=request.args.get('startup','0')=='1'
 if scope not in ['continuous','all','provisional','snippets','excerpts']:abort(400)
 reviewed=state()['reviews'];events=[]
 for index,row in enumerate(FILES):
  if scope!='all' and category(row)!=scope:continue
  # Match the exact saved event protocol for startup-included versus steady playback.
  timeline=row['steady_thresholds'][str(t)]['times_s'] if not startup and category(row)=='continuous' else row['thresholds'][str(t)]['times_s']
  for tm in timeline:
   e=event(index,t,tm);r=reviewed.get(e['id'],{});e.update(label=r.get('label','pending'),note=r.get('note',''));events.append(e)
 hist=D['aggregate']['continuous_historical_negatives'];metric=hist['thresholds'][str(t)] if startup else hist['steady_continuous']['thresholds'][str(t)]
 seconds=hist['seconds'] if startup else hist['steady_continuous']['seconds']
 originals=[c for c in P['isolated_groups']['approved_57']['clips']];context=[e for s in P['continuous_sessions'] for e in s['event_sets'].get('current_approved',{}).get('events',[])]
 return jsonify({'threshold':t,'thresholds':TH,'events':events,'reviewed_total':len([r for r in reviewed.values() if r['label']!='pending']),'historical_negative_summary':{'events':metric['events'],'seconds':seconds,'events_per_hour':metric['events_per_hour']},'positive_summary':{'isolated_hits':sum(c['peak']>=t for c in originals),'isolated_total':len(originals),'context_hits':sum(e['peak']>=t for e in context),'context_total':len(context)},'model_sha256':P['model_sha256']})
@bp.get('/<key>/false-positives/audio/<eid>')
def audio(key,eid):
 t=params();e=find_event(eid,t);row=FILES[e['file_index']];wide=request.args.get('wide')=='1';before=5 if wide else 2;after=2 if wide else 1
 prefix=e['test_padding_s'];duration=row['seconds']+prefix*2;start=max(0,e['trigger_s']-before);end=min(duration,e['trigger_s']+after);aa=round(start*16000);bb=round(end*16000)
 with wave.open(row['file']) as w:
  assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(16000,1,2)
  if prefix:
   raw=w.readframes(w.getnframes());x=np.frombuffer(raw,'<i2');x=np.pad(x,(16000,16000));raw=x[aa:bb].astype('<i2').tobytes()
  else:w.setpos(aa);raw=w.readframes(bb-aa)
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(raw)
 out.seek(0);return send_file(out,mimetype='audio/wav')
@bp.post('/<key>/false-positives/api/<eid>')
def update(key,eid):
 t=params();e=find_event(eid,t);v=request.get_json();label=v.get('label')
 if label not in ['pending','no_sunday','sunday_present','unclear']:abort(400)
 with lock:
  d=state();d['reviews'][eid]={**e,'label':label,'note':str(v.get('note',''))[:1000],'reviewed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};temp=S.with_suffix('.tmp');temp.write_text(json.dumps(d,indent=2));temp.replace(S)
 return jsonify(d['reviews'][eid])
@bp.get('/<key>/false-positives/export')
def export(key):return send_file(S,as_attachment=True,download_name='sunday-false-positive-reviews.json')

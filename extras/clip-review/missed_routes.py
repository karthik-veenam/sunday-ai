import io,json,wave,threading,datetime
from pathlib import Path
import numpy as np
from flask import Blueprint,jsonify,request,abort,send_file
B=Path('/home/djpi/sunday-openwakeword-colab/full-suite-v1');S=B/'miss-reviews.json';lock=threading.Lock();bp=Blueprint('misses',__name__)
def state():return json.loads(S.read_text())
if not S.exists():
 d=json.loads((B/'positive-results.json').read_text());rows=[]
 for group in ['historical_200','historical_32']:
  for c in d['isolated_groups'][group]['clips']:
   if c['peak']<.95:rows.append({**c,'id':len(rows)+1,'collection':group,'label':'pending','note':''})
 S.write_text(json.dumps({'clips':rows},indent=2))
@bp.get('/<key>/misses/')
def index(key):return (Path(__file__).parent/'misses.html').read_text()
@bp.get('/<key>/misses/api')
def api(key):return jsonify(state())
@bp.post('/<key>/misses/api/<int:cid>')
def update(key,cid):
 v=request.get_json();label=v.get('label')
 if label not in ['pending','clear_sunday','cut_or_unclear','not_sunday']:abort(400)
 with lock:
  d=state();c=next((c for c in d['clips'] if c['id']==cid),None)
  if not c:abort(404)
  c.update(label=label,note=str(v.get('note',''))[:1000],updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat());tmp=S.with_suffix('.tmp');tmp.write_text(json.dumps(d,indent=2));tmp.replace(S)
 return jsonify(c)
@bp.get('/<key>/misses/audio/<int:cid>')
def audio(key,cid):
 c=next((c for c in state()['clips'] if c['id']==cid),None)
 if not c:abort(404)
 with wave.open(c['file']) as w:
  width=w.getsampwidth();raw=w.readframes(w.getnframes());rate=w.getframerate()
 x=(np.frombuffer(raw,np.uint8).astype(np.int16)-128)*256 if width==1 else np.frombuffer(raw,'<i2')
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(x.astype('<i2').tobytes())
 out.seek(0);return send_file(out,mimetype='audio/wav')
@bp.get('/<key>/misses/export')
def export(key):return send_file(S,as_attachment=True,download_name='sunday-33-miss-reviews.json')
@bp.get('/<key>/misses/stats')
def stats(key):
 shards=[json.loads(p.read_text()) for p in sorted(B.glob('negative-results-shard-*.json'))];files=[];seen=set()
 for shard in shards:
  for row in shard['files']:
   if row['pcm_sha256'] not in seen:seen.add(row['pcm_sha256']);files.append(row)
 complete=(B/'negative-results.json').exists()
 thresholds=[.5,.7,.9,.95,.99,.995,.999,.9999,.99999]
 for row in files:
  p=Path(row['file']);scorepath=B/('negative-'+p.parent.name+'-'+p.stem+'-scores.npy')
  scores=np.load(scorepath);row['steady_thresholds']={}
  for t in thresholds:
   indices=np.flatnonzero(scores>=t);events=[];last=-1000;steady=[];laststeady=-1000
   for i in indices:
    tm=(int(i)+1)*.08
    if tm-last>=2:events.append(round(tm,3));last=tm
    if tm>=2.5 and tm-laststeady>=2:steady.append(round(tm,3));laststeady=tm
   row['thresholds'][str(t)]={'count':len(events),'times_s':events};row['steady_thresholds'][str(t)]={'count':len(steady),'times_s':steady}
 positive=json.loads((B/'positive-results.json').read_text());approved=positive['isolated_groups']['approved_57']['clips'];context=[e for ss in positive['continuous_sessions'] for e in ss['event_sets'].get('current_approved',{}).get('events',[])]
 historical=[r for r in files if not r['snippet_context_padding'] and not r['known_parent_excerpt'] and '/recordings/family_' not in r['file']];seconds=sum(max(0,r['seconds']-2.5) for r in historical)
 curve=[]
 for t in [.95,.99,.995,.999,.9999,.99999]:
  events=sum(r['steady_thresholds'][str(t)]['count'] for r in historical)
  curve.append({'threshold':t,'isolated_hits':sum(c['peak']>=t for c in approved),'isolated_total':len(approved),'context_hits':sum(c['peak']>=t for c in context),'context_total':len(context),'negative_events':events,'negative_seconds':seconds,'negative_events_per_hour':events*3600/seconds if seconds else None})
 return jsonify({'complete':complete,'completed_full_suite_files':files,'earlier_pi':json.loads((B.parent/'evaluation-v1/pi-results.json').read_text()),'thresholds':thresholds,'tradeoff':curve})

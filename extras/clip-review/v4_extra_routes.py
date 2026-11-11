from pathlib import Path
import json,wave,io
import numpy as np
from flask import Blueprint,jsonify,abort,request,send_file
bp=Blueprint('v4_extra',__name__);D=Path('/home/djpi/sunday-openwakeword-v4/evaluation/additional-negatives')
def rows():
 out=[];workers=[]
 for i in range(3):
  p=D/f'worker-{i}-results.json'
  if p.exists():
   d=json.loads(p.read_text());out.extend(d['files']);workers.append({'worker':i,'complete':d['complete'],'files':len(d['files'])})
 return out,workers
@bp.get('/<key>/v4-extra/')
def index(key):return (Path(__file__).parent/'v4-extra.html').read_text()
@bp.get('/<key>/v4-extra/results')
def results(key):
 threshold=request.args.get('threshold','0.99')
 if threshold not in ('0.9','0.99'):abort(400)
 rr,workers=rows();cases=[];counts={}
 for r in rr:
  events=r['thresholds'][threshold]['events'];category=r['category'];c=counts.setdefault(category,{'files':0,'seconds':0,'events':0});c['files']+=1;c['seconds']+=r['seconds'];c['events']+=len(events)
  for i,e in enumerate(events):cases.append({'file_id':r['id'],'event_index':i,'file':Path(r['file']).name,'category':category,'time_s':e['time_s'],'source_time_s':max(0,e['time_s']-r['padding_s'])+r.get('source_start_s',0),'score':e['score'],'padding_s':r['padding_s'],'label_status':r['label_status']})
 return jsonify({'workers':workers,'threshold':threshold,'cases':cases,'categories':counts,'complete':len(workers)==3 and all(w['complete'] for w in workers)})
@bp.get('/<key>/v4-extra/audio/<int:file_id>/<threshold>/<int:event_index>')
def audio(key,file_id,threshold,event_index):
 if threshold not in ('0.9','0.99'):abort(400)
 rr,_=rows();row=next((r for r in rr if r['id']==file_id),None)
 if not row:abort(404)
 ev=row['thresholds'][threshold]['events']
 if event_index>=len(ev):abort(404)
 with wave.open(row['file']) as w:raw=w.readframes(min(w.getnframes(),Path(row['file']).stat().st_size//2))
 x=np.frombuffer(raw,'<i2');t=ev[event_index]['time_s']
 if row['padding_s']:x=np.r_[np.zeros(16000*row['padding_s'],np.int16),x,np.zeros(16000*row['padding_s'],np.int16)]
 else:x=x[max(0,round((t-3.2)*16000)):min(len(x),round((t+1.8)*16000))]
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(x.tobytes())
 out.seek(0);return send_file(out,mimetype='audio/wav')

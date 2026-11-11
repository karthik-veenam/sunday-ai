import io,json,math,sys,wave,zipfile
from pathlib import Path
from flask import Blueprint,request,jsonify,send_file,abort
sys.path.insert(0,'/home/djpi/sunday')
from wake_feedback import Store
bp=Blueprint('wake_feedback',__name__)
def store():return Store()
def audio_bytes(c,start=None,end=None):
 a=c.get('start',0) if start is None else start;b=c.get('end',c['duration']) if end is None else end
 if not all(math.isfinite(v) for v in (a,b)) or not 0<=a<b<=c['duration']:raise ValueError('Invalid clip boundaries')
 with wave.open(str(store().wav_path(c['id']))) as w:
  w.setpos(round(a*16000));pcm=w.readframes(round((b-a)*16000))
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(pcm)
 return out.getvalue()
@bp.get('/<key>/wake-feedback/')
def index(key):return (Path(__file__).parent/'wake-feedback.html').read_text()
@bp.get('/<key>/wake-feedback/events')
def events(key):
 s=store();s.prune();return jsonify({'events':s.rows()})
@bp.post('/<key>/wake-feedback/events/<eid>')
def update(key,eid):
 try:
  if not request.is_json:abort(415)
  v=request.get_json();s=store();c=s.get(eid)
  if c['status'] in ('capturing','failed'):raise ValueError('Audio is not ready')
  action=v.get('action')
  if action=='report':return jsonify(s.report(eid))
  status=v.get('status',c['status'])
  if status not in ('pending','reported','approved','rejected'):raise ValueError('Invalid status')
  if status=='approved' and c['status'] not in ('reported','approved'):raise ValueError('Mark this as a false activation before approving')
  a=float(v.get('start',c['start']));b=float(v.get('end',c['end']))
  if not all(math.isfinite(x) for x in (a,b)) or not 0<=a<b<=c['duration']:raise ValueError('Invalid boundaries')
  return jsonify(s.update(eid,status=status,start=a,end=b,note=str(v.get('note',c.get('note','')))[:1000]))
 except KeyError:abort(404)
 except (ValueError,TypeError) as e:return jsonify(error=str(e)),400
@bp.get('/<key>/wake-feedback/audio/<eid>')
def audio(key,eid):
 try:
  c=store().get(eid)
  if request.args.get('correction'):
   p=store().wav_path(eid,True)
   if not p.exists():abort(404)
   return send_file(p,mimetype='audio/wav')
  a=float(request.args.get('start',0 if request.args.get('full') else c['start']));b=float(request.args.get('end',c['duration'] if request.args.get('full') else c['end']))
  return send_file(io.BytesIO(audio_bytes(c,a,b)),mimetype='audio/wav')
 except KeyError:abort(404)
 except (ValueError,TypeError):abort(400)
@bp.get('/<key>/wake-feedback/export')
def export(key):
 rows=[c for c in store().rows() if c['status']=='approved'];out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('manifest.json',json.dumps({'purpose':'Human-approved false wake activations; split by source/event when training','events':rows},indent=2))
  for c in rows:z.writestr('negatives/'+c['id']+'.wav',audio_bytes(c))
 out.seek(0);return send_file(out,mimetype='application/zip',as_attachment=True,download_name=f'sunday-feedback-{len(rows)}-negatives.zip')

@bp.get('/<key>/wake-feedback/work-log.md')
def work_log(key):
 return send_file('/home/djpi/SUNDAY_WAKE_WORD_WORK.md',mimetype='text/markdown',as_attachment=True,download_name='SUNDAY_WAKE_WORD_WORK.md')

@bp.get('/<key>/wake-feedback/v5-positives/<name>')
def v5_positive_download(key,name):
 if name not in ('REPORT.md','results.json','misses-at-099.csv','v5-positive-results.zip'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-v5/evaluation/all-positives')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

@bp.post('/<key>/wake-feedback/ha')
def ha_proxy(key):
 import base64
 if not request.is_json:abort(415)
 v=request.get_json();action=v.get('action','list')
 if action=='list':return events(key)
 if action in ('report','update'):return update(key,str(v.get('eid','')))
 if action=='audio':
  try:
   c=store().get(str(v.get('eid','')));pcm=audio_bytes(c,float(v.get('start',c['start'])),float(v.get('end',c['end'])))
   return jsonify(audio_base64=base64.b64encode(pcm).decode())
  except (KeyError,ValueError,TypeError):abort(400)
 if action=='export':
  rows=[c for c in store().rows() if c['status']=='approved'];out=io.BytesIO()
  with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
   z.writestr('manifest.json',json.dumps({'events':rows},indent=2))
   for c in rows:z.writestr('negatives/'+c['id']+'.wav',audio_bytes(c))
  return jsonify(zip_base64=base64.b64encode(out.getvalue()).decode(),filename=f'sunday-feedback-{len(rows)}-negatives.zip')
 abort(400)

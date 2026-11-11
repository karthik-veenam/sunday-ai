import io,json,wave,hashlib,secrets,threading,zipfile,os,math
from pathlib import Path
from flask import Flask,request,jsonify,send_file,abort
BASE=Path(__file__).parent
STATE=BASE/'review.json'
KEY=BASE/'access-key'
if not KEY.exists(): KEY.write_text(secrets.token_urlsafe(24));KEY.chmod(0o600)
TOKEN=KEY.read_text().strip()
lock=threading.Lock()
app=Flask(__name__)
if not STATE.exists():
 sessions=json.loads(Path('/home/djpi/sunday-wake-lab/long-session-review/sessions.json').read_text())['sessions']
 clips=[]
 for s in sessions:
  if not s['session_id'].startswith('positive_v3-'): continue
  candidates=[c for c in s['candidates'] if c['proposed_end_s']>c['proposed_start_s'] and c['proposed_end_s']<=s['duration_s']]
  for j in range(5):
   c=candidates[round((len(candidates)-1)*(j+0.5)/5)]
   clips.append(dict(id=len(clips)+1,session=s['session_id'],source=s['source'],source_sha256=hashlib.sha256(Path(s['source']).read_bytes()).hexdigest(),duration=s['duration_s'],start=c['proposed_start_s'],end=c['proposed_end_s'],initial_start=c['proposed_start_s'],initial_end=c['proposed_end_s'],status='pending',note='',candidate_id=c['candidate_id']))
 STATE.write_text(json.dumps(dict(version=1,clips=clips),indent=2))
def data(): return json.loads(STATE.read_text())
def save(d):
 p=STATE.with_suffix('.tmp');p.write_text(json.dumps(d,indent=2));p.replace(STATE)
def wav(c,start=None,end=None):
 with wave.open(c['source'],'rb') as w:
  rate=w.getframerate();a=round((c['start'] if start is None else start)*rate);b=round((c['end'] if end is None else end)*rate)
  w.setpos(a);frames=w.readframes(b-a);params=w.getparams()
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setparams(params);w.writeframes(frames)
 out.seek(0);return out
@app.before_request
def auth():
 parts=request.path.split('/')
 if request.method in ('GET','HEAD') and len(parts)==4 and parts[2]=='training-v6' and parts[3] in {'sunday-openwakeword-v6-kit.zip','Sunday_V6_one_cell.ipynb','Sunday_V6_one_cell.py','README.md','KIT_SHA256.txt'}:
  download_key=Path('/home/djpi/sunday-openwakeword-v6/download-key').read_text().strip()
  if secrets.compare_digest(parts[1],download_key):return
 if not secrets.compare_digest(request.path.split('/')[1] if len(request.path.split('/'))>1 else '',TOKEN):abort(404)
@app.after_request
def headers(r):
 r.headers['Cache-Control']='no-store';r.headers['Referrer-Policy']='no-referrer';return r
@app.get('/<key>/')
def index(key):return (BASE/'index.html').read_text()
@app.get('/<key>/api')
def api(key):return jsonify(data())
@app.post('/<key>/api/<int:cid>')
def update(key,cid):
 v=request.get_json()
 with lock:
  d=data();c=next((c for c in d['clips'] if c['id']==cid),None)
  if not c:abort(404)
  try:a=float(v['start']);b=float(v['end'])
  except (ValueError,KeyError,TypeError):abort(400)
  if not(math.isfinite(a) and math.isfinite(b) and 0<=a<b<=c['duration']):abort(400)
  status=v.get('status','pending')
  if status not in ['pending','approved','rejected']:abort(400)
  c.update(start=a,end=b,status=status,note=str(v.get('note',''))[:1000]);save(d)
 return jsonify(c)
@app.get('/<key>/audio/<int:cid>')
def audio(key,cid):
 c=next((c for c in data()['clips'] if c['id']==cid),None)
 if not c:abort(404)
 if request.args.get('full'):return send_file(c['source'],mimetype='audio/wav',conditional=True)
 return send_file(wav(c),mimetype='audio/wav')
@app.get('/<key>/export')
def export(key):
 d=data();out=io.BytesIO();approved=[c for c in d['clips'] if c['status']=='approved']
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('review.json',json.dumps(d,indent=2))
  for c in approved:z.writestr(f"positives/{c['id']:02d}-{c['session']}.wav",wav(c).read())
 out.seek(0);return send_file(out,mimetype='application/zip',as_attachment=True,download_name=f'sunday-{len(approved)}-approved.zip')
@app.get('/<key>/training-kit/<name>')
def training_download(key,name):
 if name not in ('Sunday_openWakeWord.ipynb','sunday-openwakeword-kit.zip'):abort(404)
 return send_file(Path('/home/djpi/sunday-openwakeword-colab')/name,as_attachment=True,download_name=name)

@app.get('/<key>/training-v2/<name>')
def training_v2_download(key,name):
 if name not in ('Sunday_openWakeWord_v2.ipynb','sunday-openwakeword-v2-kit.zip'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-v2')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

@app.get('/<key>/training-v3/<name>')
def training_v3_download(key,name):
 if name not in ('Sunday_openWakeWord_v3.ipynb','sunday-openwakeword-v3-kit.zip','Sunday_V3_one_cell.ipynb','Sunday_V3_one_cell.py'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-v3')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

@app.get('/<key>/training-v4/<name>')
def training_v4_download(key,name):
 if name not in ('Sunday_V4_one_cell.ipynb','Sunday_V4_one_cell.py','sunday-openwakeword-v4-kit.zip'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-v4')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

@app.get('/<key>/training-v6/<name>')
def training_v6_download(key,name):
 allowed={'sunday-openwakeword-v6-kit.zip','Sunday_V6_one_cell.ipynb','Sunday_V6_one_cell.py','README.md','KIT_SHA256.txt'}
 if name not in allowed:abort(404)
 root=Path('/home/djpi/sunday-openwakeword-v6')
 path=root/'colab/README.md' if name=='README.md' else root/name
 if not path.exists():abort(404)
 return send_file(path,as_attachment=True,download_name=name,conditional=True)

@app.get('/<key>/training-v5/<name>')
def training_v5_download(key,name):
 if name not in ('Sunday_V5_one_cell.ipynb','Sunday_V5_one_cell.py','sunday-openwakeword-v5-kit.zip'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-v5')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

@app.get('/<key>/wake-evaluation/<name>')
def evaluation_download(key,name):
 if name not in ('pi-results.json','trigger-review.zip'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-colab/evaluation-v1')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

@app.get('/<key>/full-suite/<name>')
def suite_download(key,name):
 if name not in ('full-suite-results.zip','positive-results.json','negative-results.json','positive-clips.csv','negative-files.csv','REPORT.md','positive-misses-at-095.zip'):abort(404)
 p=Path('/home/djpi/sunday-openwakeword-colab/full-suite-v1')/name
 if not p.exists():abort(404)
 return send_file(p,as_attachment=True,download_name=name)

from false_positive_routes import bp as fp_bp
app.register_blueprint(fp_bp)
from missed_routes import bp as missed_bp
app.register_blueprint(missed_bp)
from denoise_routes import bp
app.register_blueprint(bp)
from v2_results_routes import bp as v2_results_bp
app.register_blueprint(v2_results_bp)
from v4_extra_routes import bp as v4_extra_bp
app.register_blueprint(v4_extra_bp)
from wake_feedback_routes import bp as wake_feedback_bp
app.register_blueprint(wake_feedback_bp)
if __name__=='__main__':app.run(host=os.environ.get('SUNDAY_REVIEW_BIND','127.0.0.1'),port=8766,threaded=True)

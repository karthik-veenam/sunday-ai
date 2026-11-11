import json,io,zipfile,threading
from pathlib import Path
from flask import Blueprint,jsonify,request,abort,send_file
B=Path(__file__).parent;D=B/'denoise';S=D/'review.json';lock=threading.Lock()
bp=Blueprint('denoise',__name__)
def data():return json.loads(S.read_text())
@bp.get('/<key>/denoise/')
def index(key):
 s=(B/'index.html').read_text().replace('Sunday · Clip review','Sunday · Noise reduction review').replace('Sunday, precisely.','Sunday, a little clearer.').replace('Five candidates from each of eight sessions. Keep one complete “Sunday”, with no adjacent words. Listen, adjust the boundaries, then mark green. Original recordings stay intact. The Roopa-labelled session needs a speaker check.','20 noise-reduced versions selected from your 38 approved clips. Compare each with its original. Approve only if the full word remains natural and clear. Your 38 original approvals are preserved.').replace('/ 40 approved','/ 20 approved').replace('Download approved WAVs + labels','Download 38 originals + approved clean clips').replace('<button class="context">Play with context</button>','<button class="original">▶ Compare original</button><button class="context">Clean context</button>').replace("el.querySelector('.play').onclick=()=>play(c);", "el.querySelector('.play').onclick=()=>play(c);el.querySelector('.original').onclick=()=>{audio.pause();stopAt=null;audio.src=base+'original/'+c.id;audio.play()};").replace("#${String(c.id).padStart(2,'0')} ·", "#${String(c.id).padStart(2,'0')} (original #${c.parent_id}) ·")
 return s
@bp.get('/<key>/denoise/api')
def api(key):return jsonify(data())
@bp.post('/<key>/denoise/api/<int:cid>')
def update(key,cid):
 import math
 v=request.get_json()
 with lock:
  d=data();c=next((x for x in d['clips'] if x['id']==cid),None)
  if not c:abort(404)
  try:a=float(v['start']);b=float(v['end'])
  except (ValueError,TypeError,KeyError):abort(400)
  status=v.get('status','pending')
  if not(math.isfinite(a) and math.isfinite(b) and 0<=a<b<=c['duration']) or status not in ('pending','approved','rejected'):abort(400)
  c.update(start=a,end=b,status=status,note=str(v.get('note',''))[:1000]);tmp=S.with_suffix('.tmp');tmp.write_text(json.dumps(d,indent=2));tmp.replace(S)
 return jsonify(c)
def clip(cid):
 c=next((x for x in data()['clips'] if x['id']==cid),None)
 if not c:abort(404)
 return c
@bp.get('/<key>/denoise/audio/<int:cid>')
def audio(key,cid):
 from app import wav
 c=clip(cid)
 if request.args.get('full'):return send_file(c['source'],mimetype='audio/wav',conditional=True)
 return send_file(wav(c),mimetype='audio/wav')
@bp.get('/<key>/denoise/original/<int:cid>')
def original(key,cid):return send_file(clip(cid)['original_clip'],mimetype='audio/wav')
@bp.get('/<key>/denoise/export')
def export(key):
 from app import wav
 d=data();out=io.BytesIO();approved=[c for c in d['clips'] if c['status']=='approved']
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  with zipfile.ZipFile(D/'approved-originals.zip') as orig:
   z.writestr('original-review.json',orig.read('review.json'))
   for name in orig.namelist():
    if name.startswith('positives/') and name.endswith('.wav'):z.writestr(name,orig.read(name))
  z.writestr('denoise-review.json',json.dumps(d,indent=2))
  z.writestr('README.txt','38 original utterances plus approved noise-reduced derivatives. Keep each derivative with its original parent and session when splitting train/validation/test. All these clips are designated training data by the user.\n')
  for c in approved:z.writestr(f"positives/clean-{c['id']:02d}-parent-{c['parent_id']:02d}.wav",wav(c).read())
 out.seek(0);return send_file(out,mimetype='application/zip',as_attachment=True,download_name=f'sunday-{38+len(approved)}-training.zip')

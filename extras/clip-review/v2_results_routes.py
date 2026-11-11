from pathlib import Path
import io,json,wave
from flask import Blueprint,abort,jsonify,send_file
bp=Blueprint('v2_results',__name__);B=Path('/home/djpi/sunday-openwakeword-v2/evaluation')
@bp.get('/<key>/v2-fresh/')
def index(key):return (Path(__file__).parent/'v2-fresh.html').read_text()
@bp.get('/<key>/v2-fresh/results')
def results(key):
 p=B/'fresh-results.json'
 if not p.exists():return jsonify({'ready':False})
 return jsonify({'ready':True,**json.loads(p.read_text())})
@bp.get('/<key>/v2-fresh/audio/<int:idx>')
def audio(key,idx):
 p=B/'fresh-results.json'
 if not p.exists():abort(404)
 d=json.loads(p.read_text());events=d['files'][0]['events']
 if not 0<=idx<len(events):abort(404)
 t=events[idx]['time_s']
 with wave.open(str(B/'fresh-recording.wav')) as w:
  rate=w.getframerate();a=max(0,round((t-3.2)*rate));b=min(w.getnframes(),round((t+1.8)*rate));w.setpos(a);raw=w.readframes(b-a)
 out=io.BytesIO()
 with wave.open(out,'wb') as w:w.setparams((1,2,rate,0,'NONE','not compressed'));w.writeframes(raw)
 out.seek(0);return send_file(out,mimetype='audio/wav')
@bp.get('/<key>/v2-fresh/full-audio')
def full(key):return send_file(B/'fresh-recording.wav',mimetype='audio/wav',conditional=True)

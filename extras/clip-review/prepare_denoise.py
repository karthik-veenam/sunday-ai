import json,zipfile,wave,io,hashlib,subprocess,collections
from pathlib import Path
import numpy as np
B=Path(__file__).parent;O=B/'denoise';O.mkdir(exist_ok=True)
Z=Path('/tmp/codex-remote-attachments/01a158a1-11af-74a0-aaa4-22025d1947e0/7717c7bb-abec-4dff-b7eb-660f5889b6e0/1-sunday-38-approved.zip')
(O/'approved-originals.zip').write_bytes(Z.read_bytes())
def read(p):
 with wave.open(str(p),'rb') as w:return np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(float)/32768,w.getframerate()
def write(p,x,sr):
 with wave.open(str(p),'wb') as w:w.setparams((1,2,sr,0,'NONE','not compressed'));w.writeframes(np.round(np.clip(x,-1,32767/32768)*32768).astype('<i2').tobytes())
with zipfile.ZipFile(Z) as z:
 d=json.loads(z.read('review.json'));approved=[c for c in d['clips'] if c['status']=='approved']
 for c in approved:
  name=f"{c['id']:02d}-{c['session']}.wav";p=O/name;p.write_bytes(z.read('positives/'+name));c['original_clip']=str(p)
cache={};rank=[]
for c in approved:
 src=c['source']
 if src not in cache:
  x,sr=read(src);frames=x[:len(x)//320*320].reshape(-1,320);rms=np.sqrt(np.mean(frames**2,axis=1));floor=float(np.percentile(rms,20));cache[src]=(x,sr,floor)
 x,sr,floor=cache[src];clip,_=read(c['original_clip']);rms=float(np.sqrt(np.mean(clip**2)));peak=float(np.max(np.abs(clip)));snr=20*np.log10(max(rms,1e-9)/max(floor,1e-9));clipping=float(np.mean(np.abs(clip)>=.99));duration=len(clip)/sr
 score=float(snr-100*clipping-(10 if duration<.3 or duration>1.3 else 0))
 c['quality']={'score':score,'speech_to_session_floor_db_proxy':float(snr),'rms':rms,'peak':peak,'clipped_fraction':clipping,'duration_s':duration};rank.append(c)
# Retain session diversity, selecting the strongest three at most from each session.
selected=[];counts=collections.Counter()
for c in sorted(rank,key=lambda c:c['quality']['score'],reverse=True):
 if counts[c['session']]>=3:continue
 selected.append(c);counts[c['session']]+=1
 if len(selected)==20:break
clips=[]
for j,c in enumerate(selected,1):
 src=c['source'];x,sr,floor=cache[src];processed=O/(c['session']+'-denoised-session.wav')
 if str(processed) not in globals().setdefault('processed_paths',set()):
  nf=float(np.clip(20*np.log10(max(floor,1e-9)),-65,-30))
  subprocess.run(['ffmpeg','-v','error','-y','-i',src,'-af',f'apad=pad_dur=0.1,afftdn=nr=8:nf={nf:.2f}:tn=1:gs=5,atrim=start_sample=400:end_sample={len(x)+400},asetpts=PTS-STARTPTS','-ar',str(sr),'-ac','1','-c:a','pcm_s16le',str(processed)],check=True)
 processed_paths.add(str(processed))
 y,_=read(processed);assert len(y)==len(x)
 a=round(c['start']*sr);b=round(c['end']*sr);assert b>a
 # Measure alignment: no timestamp shifts are silently applied.
 ref=x[a:b];out=y[a:b];lags=range(-400,401);corr=[]
 for lag in lags:
  r=ref[max(0,lag):min(len(ref),len(ref)+lag)];v=out[max(0,-lag):min(len(out),len(out)-lag)];corr.append(float(np.dot(r,v)))
 lag=list(lags)[int(np.argmax(corr))]
 clip_path=O/f'denoised-{j:02d}.wav';write(clip_path,out,sr)
 clips.append({**c,'id':j,'parent_id':c['id'],'source':str(processed),'original_session_source':src,'status':'pending','note':'','initial_start':c['start'],'initial_end':c['end'],'processing':'FFmpeg afftdn, reduction 8dB, tracked session noise floor, gain smoothing 5; full session processed before cropping','alignment_lag_samples':lag,'source_sha256':hashlib.sha256(processed.read_bytes()).hexdigest()})
state={'version':1,'selection':'Quality proxy descending; maximum 3 per session for diversity. Not a linguistic or perceptual ranking.','original_approved_count':38,'clips':clips,'ranking':[{k:c[k] for k in ['id','session','quality']} for c in sorted(rank,key=lambda c:c['quality']['score'],reverse=True)]}
(O/'review.json').write_text(json.dumps(state,indent=2));print('Selected',len(clips),'sessions',dict(counts));print('alignment lags',[(c['parent_id'],c['alignment_lag_samples']) for c in clips])

"""Offline GCC-PHAT for synchronized two-slot S16 PCM. No Sunday interfaces."""
import argparse,json,math,wave
from pathlib import Path
import numpy as np

def read_wav(path):
 with wave.open(str(path),'rb') as w:
  if w.getnchannels()!=2 or w.getsampwidth()!=2 or w.getcomptype()!='NONE':
   raise ValueError('Requires uncompressed two-channel signed 16-bit PCM WAV; mono duplication is not stereo.')
  rate=w.getframerate();pcm=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').reshape(-1,2).copy()
 if rate not in (16000,48000):raise ValueError('Use 16000 or 48000 Hz diagnostic recordings.')
 if len(pcm)<int(.064*rate):raise ValueError('Recording is too short.')
 return pcm,rate

def gcc_phat(a,b,rate,spacing_mm,interp=16):
 """delay = arrival(channel0) - arrival(channel1); positive means ch0 later."""
 a=np.asarray(a,dtype=float);b=np.asarray(b,dtype=float)
 a=a-a.mean();b=b-b.mean();window=np.hanning(len(a));n=1<<(2*len(a)-1).bit_length()
 A=np.fft.rfft(a*window,n);B=np.fft.rfft(b*window,n);cross=A*np.conj(B)
 freq=np.fft.rfftfreq(n,1/rate);mask=(freq>=250)&(freq<=min(6000,rate*.45))
 floor=max(float(np.max(np.abs(cross)))*1e-8,1e-20)
 spec=np.where(mask,cross/np.maximum(np.abs(cross),floor),0)
 cc=np.fft.irfft(spec,n*interp)
 physical=spacing_mm/1000/343.;wide=min(n*interp//2,int(math.ceil(max(.002,3*physical)*rate*interp)))
 curve=np.concatenate((cc[-wide:],cc[:wide+1]));lags=np.arange(-wide,wide+1)/(rate*interp)
 # Absolute correlation also handles opposite microphone polarity.
 score=np.abs(curve);index=int(np.argmax(score));delay=float(lags[index]);physical_ok=abs(delay)<=physical+1/(rate*interp)
 excluded=np.abs(lags-delay)>max(1/rate,physical*.25)
 side=float(np.sqrt(np.mean(score[excluded]**2))) if np.any(excluded) else float(np.max(score))
 prominence=float(score[index]/max(side,1e-20))
 # Time-aligned normalized correlation is a sanity check, not calibrated confidence.
 shift=int(round(delay*rate));x=a[max(shift,0):len(a)+min(shift,0)];y=b[max(-shift,0):len(b)-max(shift,0)]
 corr=float(abs(np.dot(x,y))/max(np.linalg.norm(x)*np.linalg.norm(y),1e-20)) if len(x)>1 else 0.
 return {'delay_us':delay*1e6,'physical_ok':bool(physical_ok),'peak_to_sidelobe_rms':prominence,'aligned_correlation':corr}

def analyze(pcm,rate,spacing_mm,channel0_side=None,center_offset_us=0.,min_dbfs=-55.,center_fraction=.25):
 if not 5<=spacing_mm<=200:raise ValueError('Measure microphone spacing: 5..200 mm supported.')
 if channel0_side not in (None,'left','right'):raise ValueError('Channel0 side must be left or right after calibration.')
 if not 0<center_fraction<1:raise ValueError('Center fraction must be between zero and one.')
 max_us=spacing_mm/1000/343.*1e6
 if abs(center_offset_us)>max_us:raise ValueError('Center offset exceeds acoustic bound; investigate routing/skew.')
 rms=np.sqrt(np.mean(pcm.astype(float)**2,axis=0));levels=20*np.log10(np.maximum(rms,1e-6)/32768)
 duplicate_fraction=float(np.mean(pcm[:,0]==pcm[:,1]));clips=np.mean(np.abs(pcm.astype(np.int32))>=32760,axis=0)
 block=int(rate*.064);hop=block//2;windows=[]
 for start in range(0,len(pcm)-block+1,hop):
  x=pcm[start:start+block];lev=20*np.log10(np.maximum(np.sqrt(np.mean(x.astype(float)**2,axis=0)),1e-6)/32768)
  r={'start_seconds':round(start/rate,4),'direction':None}
  if min(lev)<min_dbfs:r['status']='QUIET'
  elif max(lev)-min(lev)>20:r['status']='CHANNEL_IMBALANCE'
  elif np.any(np.mean(np.abs(x.astype(np.int32))>=32760,axis=0)>.01):r['status']='CLIPPED'
  elif np.mean(x[:,0]==x[:,1])>.999:r['status']='DUPLICATED_OR_DEGENERATE'
  else:
   g=gcc_phat(x[:,0],x[:,1],rate,spacing_mm);r.update(g)
   if not g['physical_ok']:r['status']='OUT_OF_RANGE'
   elif g['aligned_correlation']<.35 or g['peak_to_sidelobe_rms']<2:r['status']='AMBIGUOUS'
   else:
    r['status']='VALID';delay=g['delay_us']-center_offset_us
    if abs(delay)>max_us+1e6/rate/16:r['status']='OUT_OF_RANGE'
    elif channel0_side:
     if abs(delay)<=max_us*center_fraction:r['direction']='CENTER'
     else:r['direction']=('RIGHT' if delay>0 else 'LEFT') if channel0_side=='left' else ('LEFT' if delay>0 else 'RIGHT')
  windows.append(r)
 valid=[w for w in windows if w['status']=='VALID'];votes={k:sum(w['direction']==k for w in valid) for k in ('LEFT','CENTER','RIGHT')}
 direction=max(votes,key=votes.get);fraction=votes[direction]/max(1,len(valid));valid_fraction=len(valid)/max(1,len(windows))
 if duplicate_fraction>.999:status='DUPLICATED_OR_DEGENERATE';direction=None
 elif len(valid)<3 or valid_fraction<.25:status='INSUFFICIENT_RELIABLE_AUDIO';direction=None
 elif not channel0_side:status='UNCALIBRATED_CHANNEL_ORDER';direction=None
 elif fraction<.65:status='AMBIGUOUS_OR_MOVING';direction=None
 else:status='CLASSIFIED'
 return {'status':status,'direction':direction,'sample_rate':rate,'seconds':len(pcm)/rate,'spacing_mm':spacing_mm,
  'channel0_side':channel0_side,'center_offset_us':center_offset_us,'max_acoustic_delay_us':max_us,
  'rms_dbfs':levels.tolist(),'clipped_fraction':clips.tolist(),'identical_sample_fraction':duplicate_fraction,
  'valid_windows':len(valid),'total_windows':len(windows),'valid_fraction':valid_fraction,'votes':votes,
  'vote_fraction':fraction if channel0_side else None,'median_raw_delay_us':float(np.median([w['delay_us'] for w in valid])) if valid else None,
  'note':'LEFT/RIGHT are from the robot point of view. Null direction means rejected/uncalibrated, never CENTER. Scores are heuristics, not probabilities. Two mics cannot resolve front/back or identify a speaker.','windows':windows}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('wav',type=Path);p.add_argument('--spacing-mm',type=float,required=True)
 p.add_argument('--channel0-side',choices=['left','right']);p.add_argument('--center-offset-us',type=float,default=0)
 p.add_argument('--min-dbfs',type=float,default=-55);p.add_argument('--json',type=Path)
 a=p.parse_args();pcm,rate=read_wav(a.wav);result=analyze(pcm,rate,a.spacing_mm,a.channel0_side,a.center_offset_us,a.min_dbfs)
 if a.json:a.json.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='windows'},indent=2))
if __name__=='__main__':main()

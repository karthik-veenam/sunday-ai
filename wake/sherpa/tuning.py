"""Shared lightweight tuning validation/storage (no inference imports)."""
import json,math,os,tempfile
from pathlib import Path
FILE=Path('/home/djpi/sunday-sherpa-verifier/state/tuning.json')
def validate(boost,threshold):
 for name,value in [('boost',boost),('threshold',threshold)]:
  if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError(name+' must be a finite number.')
 if not 0<=boost<=100:raise ValueError('Boost must be between 0 and 100 in this control.')
 if not 0<=threshold<=1:raise ValueError('Threshold must be between 0 and 1.')
 return {'boost':float(boost),'threshold':float(threshold)}
def read():
 if not FILE.exists():return {'boost':3.0,'threshold':.1}
 d=json.loads(FILE.read_text());return validate(d['boost'],d['threshold'])
def save(values):
 values=validate(values['boost'],values['threshold']);fd,name=tempfile.mkstemp(dir=FILE.parent,prefix='.tuning-')
 try:
  with os.fdopen(fd,'w') as f:json.dump(values,f);f.write('\n');f.flush();os.fsync(f.fileno())
  os.chmod(name,0o600);os.replace(name,FILE)
 finally:
  if os.path.exists(name):os.unlink(name)

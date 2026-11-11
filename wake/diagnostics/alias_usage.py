"""Read-only counts of actual accepted verifier aliases, from retained verdicts."""
import json,threading
from datetime import datetime
from pathlib import Path
VERDICTS=Path('/home/djpi/sunday-wake-verifier/verdicts.jsonl')
class AliasUsage:
 def __init__(self,path=VERDICTS):self.path=Path(path);self.lock=threading.Lock();self.stamp=None;self.cached=None
 def snapshot(self):
  with self.lock:
   try:
    st=self.path.stat();stamp=(st.st_ino,st.st_mtime_ns,st.st_size)
    if stamp==self.stamp:return self.cached
    counts={};latest=None;first=None;seen=set()
    with self.path.open() as f:
     for line in f:
      try:
       row=json.loads(line)
       if not isinstance(row,dict):continue
       at=datetime.fromisoformat(row['t'])
       if at.tzinfo is None:continue
       if first is None or at<first:first=at
       alias=row.get('matched')
       if row.get('mode')!='enforce' or row.get('accepted') is not True or not isinstance(alias,str) or not alias:continue
       event=row.get('event_id')
       if event:
        if event in seen:continue
        seen.add(event)
       counts[alias]=counts.get(alias,0)+1
       if latest is None or at>=latest[0]:latest=(at,alias)
      except (ValueError,KeyError,TypeError):continue
    result={'available':True,'counts':counts,'latest':{'alias':latest[1],'at':latest[0].isoformat()} if latest else None,'since':first.isoformat() if first else None,'scope':'Accepted wakes in retained verifier history; one credited alias per wake.'}
    self.stamp,self.cached=stamp,result
    return result
   except OSError:
    return {'available':False,'counts':{},'latest':None,'since':None,'scope':'Verifier history is unavailable.'}

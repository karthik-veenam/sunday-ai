"""Private rejected-wake corpus. No recording, resampling, or inference here."""
import base64,hashlib,json,logging,os,queue,re,sqlite3,threading,time,wave
from pathlib import Path
ROOT=Path('/home/djpi/sunday-wake-verifier/negative-dataset')
ID=re.compile(r'^[a-f0-9]{32}$')
LABELS=('unreviewed','background','wake_word_present')
class Dataset:
 def __init__(self,root=ROOT):self.root=Path(root)
 def connect(self):
  self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
  db=sqlite3.connect(self.root/'index.sqlite3',timeout=2)
  os.chmod(self.root/'index.sqlite3',0o600)
  db.row_factory=sqlite3.Row
  db.execute('pragma journal_mode=WAL')
  db.execute('create table if not exists clips(id text primary key, captured text not null, transcript text not null, duration real not null, bytes integer not null, review text not null, reviewed_at real, metadata text not null)')
  db.execute('create index if not exists clips_captured on clips(captured desc,id)')
  return db
 def path(self,ident):
  if not isinstance(ident,str) or not ID.fullmatch(ident):raise ValueError('Invalid clip ID.')
  return self.root/(ident+'.wav')
 def save(self,pcm,record,config):
  if record.get('mode')!='enforce' or record.get('accepted') is not False or record.get('error'):return False
  ident=record['event_id'];path=self.path(ident)
  if not isinstance(pcm,bytes) or not pcm or len(pcm)%2 or len(pcm)>320000:raise ValueError('Invalid PCM buffer.')
  db=self.connect();tmp=self.root/(ident+'.tmp')
  try:
   if db.execute('select 1 from clips where id=?',(ident,)).fetchone():return False
   metadata={'verdict':record,'verifier':{'provider':record.get('provider','whisper'),'model':record.get('verifier_model','tiny.en-q5_1'),'audio_ctx':config.get('audio_ctx',128) if record.get('provider','whisper')=='whisper' else None,'prompt':'Sunday' if record.get('provider','whisper')=='whisper' else None,'aliases':list(config.get('aliases',[]))},'audio':{'sample_rate':16000,'channels':1,'sample_width':2,'format':'PCM_S16LE','samples':len(pcm)//2,'sha256_pcm':hashlib.sha256(pcm).hexdigest()},'dataset_version':1}
   fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
   with os.fdopen(fd,'wb') as f:
    with wave.open(f,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(pcm)
    f.flush();os.fsync(f.fileno())
   os.replace(tmp,path)
   db.execute('insert into clips values(?,?,?,?,?,?,?,?)',(ident,str(record['t']),str(record.get('text','')).strip(),len(pcm)/32000,path.stat().st_size,'unreviewed',None,json.dumps(metadata)))
   db.commit();return True
  finally:db.close();tmp.unlink(missing_ok=True)
 def listing(self,offset=0,limit=20,review='all'):
  if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:raise ValueError('Invalid page.')
  if review not in ('all',)+LABELS:raise ValueError('Invalid review filter.')
  db=self.connect()
  try:
   where,args=('',[]) if review=='all' else (' where review=?',[review])
   total=db.execute('select count(*) from clips'+where,args).fetchone()[0]
   rows=[dict(r) for r in db.execute("select id,captured,transcript,duration,bytes,review,reviewed_at,json_extract(metadata,'$.verdict.provider') as verifier_provider from clips"+where+' order by captured desc,id desc limit ? offset ?',args+[min(max(1,limit),50),offset])]
   stats=db.execute('select count(*),coalesce(sum(bytes),0) from clips').fetchone()
   return {'clips':rows,'total':total,'offset':offset,'limit':min(max(1,limit),50),'dataset_count':stats[0],'audio_bytes':stats[1],'capture':'Rejected enforce-mode wake windows only. Original 16 kHz mono PCM; no automatic expiry.'}
  finally:db.close()
 def audio(self,ident):
  path=self.path(ident);db=self.connect()
  try:
   row=db.execute('select id from clips where id=?',(ident,)).fetchone()
   if not row or path.is_symlink() or not path.is_file():raise ValueError('Clip not found.')
   return {'id':ident,'mime':'audio/wav','audio_base64':base64.b64encode(path.read_bytes()).decode()}
  finally:db.close()
 def review(self,ident,label):
  self.path(ident)
  if label not in LABELS:raise ValueError('Invalid review label.')
  db=self.connect()
  try:
   if db.execute('update clips set review=?,reviewed_at=? where id=?',(label,time.time(),ident)).rowcount!=1:raise ValueError('Clip not found.')
   db.commit();return {'id':ident,'review':label}
  finally:db.close()
 def delete(self,ident):
  path=self.path(ident);db=self.connect();trash=self.root/(ident+'.deleting')
  try:
   db.execute('begin immediate')
   if not db.execute('select id from clips where id=?',(ident,)).fetchone():raise ValueError('Clip not found.')
   if path.is_symlink():raise ValueError('Invalid clip file.')
   if path.exists():os.replace(path,trash)
   try:db.execute('delete from clips where id=?',(ident,));db.commit()
   except BaseException:
    db.rollback()
    if trash.exists():os.replace(trash,path)
    raise
   trash.unlink(missing_ok=True);return {'id':ident,'deleted':True}
  finally:db.close()

_jobs=queue.Queue(maxsize=128)
_worker=None
_lock=threading.Lock()
def _run():
 dataset=Dataset()
 while True:
  item=_jobs.get()
  try:dataset.save(*item)
  except Exception as e:logging.error('Negative wake dataset write failed (%s); wake processing unaffected.',type(e).__name__)
  finally:_jobs.task_done()
def enqueue(pcm,record,config):
 """Nonblocking producer: buffer a copy; all disk I/O is on one daemon worker."""
 global _worker
 if record.get('mode')!='enforce' or record.get('accepted') is not False:return
 with _lock:
  if _worker is None:
   _worker=threading.Thread(target=_run,name='negative-wake-writer',daemon=True);_worker.start()
 try:_jobs.put_nowait((pcm,dict(record),dict(config)))
 except queue.Full:logging.error('Negative wake dataset queue full; clip not saved. Wake processing unaffected.')

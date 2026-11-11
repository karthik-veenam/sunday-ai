import argparse,json,asyncio
from pathlib import Path
from .store import Store,TABLES
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'state/proactivity_v1.db');sub=p.add_subparsers(dest='command',required=True)
 for table in sorted(TABLES):
  a=sub.add_parser(table);a.add_argument('--state',action='append');a.add_argument('--limit',type=int,default=30)
 sub.add_parser('flags');a=sub.add_parser('chain');a.add_argument('id');
 for view in ('would','delivered','suppressed','failed'):
  a=sub.add_parser(view);a.add_argument('--hours',type=float,default=24)
 a=sub.add_parser('telegram-fixtures');a.add_argument('--output',type=Path,default=ROOT/'deployment/TELEGRAM_RELEASE_E2E.json')
 a=sub.add_parser('rollback-telegram');a.add_argument('--config',type=Path,default=ROOT/'config.json')
 a=sub.add_parser('fixtures');a.add_argument('--output',type=Path,default=ROOT/'deployment/PROACTIVITY_V1_E2E.json')
 a=p.parse_args()
 if a.command=='rollback-telegram':
  import os
  cfg=json.loads(a.config.read_text());cfg.update(proactive_tell_enabled=False,proactive_ask_enabled=False,proactive_act_enabled=False,proactive_voice_enabled=False)
  temp=a.config.with_suffix('.rollback.tmp');temp.write_text(json.dumps(cfg,indent=2)+'\n');temp.chmod(0o600);os.replace(temp,a.config);print('Proactive Telegram TELL/ASK disabled. Cognition, normal conversations and audit history preserved.');return
 if a.command=='telegram-fixtures':
  from .release_fixtures import run
  print(json.dumps(asyncio.run(run(a.output)),indent=2));return
 if a.command=='fixtures':
  from .fixtures import run
  print(json.dumps(asyncio.run(run(a.output)),indent=2));return
 store=Store(a.db)
 try:
  if a.command in TABLES:
   where=' where state in ('+','.join('?' for _ in a.state)+')' if a.state else '';result=[store.decode(r) for r in store.db.execute('select * from '+a.command+where+' order by created desc,id desc limit ?',(*(a.state or []),max(1,min(200,a.limit))))]
  elif a.command=='flags':
   from config import Config
   from .service import FLAGS
   c=Config.load(str(ROOT/'config.json'));result={k:getattr(c,k) for k in FLAGS};result['memory_read_enabled']=c.memory_read_enabled
  elif a.command=='chain':result=store.chain(a.id)
  else:
   from .inspection import inspect
   from config import Config
   from types import SimpleNamespace
   result=inspect(SimpleNamespace(store=store,delivery=None),{'view':a.command,'hours':a.hours,'limit':100},Config.load(str(ROOT/'config.json'))) 
  print(json.dumps(result,indent=2,ensure_ascii=False))
 finally:store.close()
if __name__=='__main__':main()

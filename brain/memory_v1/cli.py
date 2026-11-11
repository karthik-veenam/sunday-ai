"""Inspect learned memory without editing SQLite manually."""
import argparse,asyncio,json
from pathlib import Path
from .store import Store,js
from .behavior import learn_patterns,temporal_boundaries
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'state/memory_v1.db');p.add_argument('--config',type=Path,default=ROOT/'config.json');sub=p.add_subparsers(dest='command',required=True)
 for name in ['inbox','memories','evidence','audit','runs','retrievals','versions']:
  a=sub.add_parser(name);a.add_argument('--limit',type=int,default=30)
 sub.add_parser('flags');a=sub.add_parser('retrieve');a.add_argument('query');a.add_argument('--person',action='append',default=[]);a.add_argument('--entity',action='append',default=[]);a.add_argument('--area',action='append',default=[]);a.add_argument('--limit',type=int,default=5)
 a=sub.add_parser('chain');a.add_argument('memory_id');sub.add_parser('render');sub.add_parser('boundaries');a=sub.add_parser('consolidate');a.add_argument('--limit',type=int,default=12);sub.add_parser('patterns');a=sub.add_parser('retry');a.add_argument('candidate_id')
 a=p.parse_args();store=Store(a.db)
 try:
  if a.command in {'inbox','memories','evidence','audit','runs','retrievals','versions'}:result=store.inspect(a.command,a.limit)
  elif a.command=='flags':
   from config import Config
   c=Config.load(str(a.config));result={k:getattr(c,k) for k in ['memory_write_enabled','memory_read_enabled','memory_climate_learning_enabled','memory_consolidation_model','memory_max_candidates_per_run','memory_evidence_interval_seconds','memory_nightly_interval_seconds','memory_weekly_interval_seconds','memory_pattern_min_samples']}
  elif a.command=='retrieve':result=store.retrieve(a.query,people=a.person,entities=a.entity,areas=a.area,limit=a.limit)
  elif a.command=='chain':result=store.chain(a.memory_id)
  elif a.command=='render':store.render_wiki(a.db.parent/'memory_v1_wiki');result={'rendered':str(a.db.parent/'memory_v1_wiki')}
  elif a.command=='boundaries':result=[b for row in store.db.execute("select data from memories where type='PATTERN_EXPECTATION' and status='ACTIVE'") for b in temporal_boundaries(json.loads(row[0]))]
  elif a.command=='retry':
   with store.db:
    row=store.db.execute("update inbox set state='NEW',error=NULL where id=? and state='ERROR'",(a.candidate_id,));result={'queued':row.rowcount}
  else:
   from config import Config
   c=Config.load(str(a.config))
   if not c.memory_write_enabled:raise ValueError('Memory writes disabled')
   if a.command=='patterns':result=learn_patterns(store,min_samples=c.memory_pattern_min_samples,climate_learning=c.memory_climate_learning_enabled)
   else:
    from .consolidation import Consolidator
    result=asyncio.run(Consolidator(store,c).nightly(a.limit))
  print(json.dumps(result,ensure_ascii=False,indent=2))
 finally:store.close()
if __name__=='__main__':main()

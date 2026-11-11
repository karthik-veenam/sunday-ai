"""Archive obsolete autonomous work, preserving all evidence and explicit commitments."""
def migrate(store):
 if store.meta('proactivity_v2_migration'):return store.meta('proactivity_v2_migration')
 counts={}
 with store.lock,store.db:
  for table in ['episodes','wakes','temporal','watches','proposals','deliveries']:
   changed=0
   for row in store.rows(table,('OPEN','PENDING','READY','PROPOSED','GATE_APPROVED','RETRY','ACTIVE','FAILED','PROCESSING'),1000):
    if row['state'] not in {'OPEN','PENDING','READY','PROPOSED','GATE_APPROVED','RETRY','ACTIVE','FAILED','PROCESSING'}:continue
    d=row['data'];authority=d.get('authority') or d.get('wake',{}).get('authority')
    if table=='deliveries':
     proposal=store.get('proposals',d.get('proposal_id',''));authority=(proposal or {}).get('data',{}).get('wake',{}).get('authority')
    if authority=='USER_EXPLICIT' or table=='temporal' and d.get('kind')=='COMMITMENT_DUE':continue
    store.change(table,row['id'],state='ARCHIVED_V1');changed+=1
   counts[table]=changed
  receipt={'at':store.clock(),'archived':counts,'preserved':'raw evidence, audit, outcomes, completed receipts, explicit user commitments and input configuration'}
  store.set_meta('proactivity_v2_migration',receipt)
 return receipt

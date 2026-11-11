"""Explicit user-authored phrases; HA owns all action sequences."""
import re
from pathlib import Path
import yaml

BLUEPRINT = 'sunday/when_i_say.yaml'
def normalize(text):
    return ' '.join(re.sub(r'[^\w\s]', ' ', text.casefold()).split())

class Loader(yaml.SafeLoader): pass
Loader.add_multi_constructor('!', lambda loader, tag, node: loader.construct_scalar(node) if isinstance(node,yaml.ScalarNode) else loader.construct_sequence(node) if isinstance(node,yaml.SequenceNode) else loader.construct_mapping(node))

class PhraseAutomations:
    def __init__(self, ha, root):
        self.ha, self.path = ha, Path(root)/'automations.yaml'
        self.stamp = None
        self.rules = []
        self.refresh()

    def refresh(self):
        try:
            stat=self.path.stat(); stamp=(stat.st_mtime_ns,stat.st_size)
            if stamp==self.stamp:return
            rows=yaml.load(self.path.read_text(),Loader=Loader) or []
            rules=[]
            for row in rows:
                bp=row.get('use_blueprint',{})
                if bp.get('path')!=BLUEPRINT:continue
                inputs=bp.get('input',{})
                phrases={normalize(p) for p in str(inputs.get('phrases','')).splitlines() if normalize(p)}
                if phrases and row.get('id'):rules.append({'id':str(row['id']),'phrases':phrases,'telegram':inputs.get('telegram',False) is True,'reply':str(inputs.get('reply','On it.'))[:300] or 'On it.'})
            self.rules,self.stamp=rules,stamp
        except (OSError,yaml.YAMLError,AttributeError,TypeError):
            self.rules=[] # Never use stale rules after invalid/removed configuration.

    async def attempt(self,request,output):
        source=request.get('source','ha')
        if source not in ('voice','lva','telegram'):return None
        self.refresh()
        phrase=normalize(request['text'])
        matches=[r for r in self.rules if phrase in r['phrases'] and (source!='telegram' or r['telegram'])]
        if not matches:return None
        # Only matching phrases incur an HA read; disabling takes effect immediately.
        states=await self.ha.get_states()
        enabled={str(s.get('attributes',{}).get('id')) for s in states if s.get('entity_id','').startswith('automation.') and s.get('state')=='on'}
        matches=[r for r in matches if r['id'] in enabled]
        if not matches:return None
        record={'id':output.request_id,'channel':source,'input':request['text'],'mode':'execute','plan':[],'reason':'Explicit user-authored phrase binding','handled':True,'decision':'PHRASE_AUTOMATION','text':request['text'],'request_id':output.request_id,'source':source,'reply':''}
        if len(matches)>1:
            record['reply']='That phrase matches more than one automation. Please give them different phrases.'
            return record
        if output.read_only or not self.ha.actions_enabled:
            record['reply']='Actions are disabled, so I haven’t run that automation.'
            return record
        rule=matches[0]
        async def dispatch():
            try:
                await self.ha.request('POST','/api/events/sunday_when_i_say',{'automation_id':rule['id'],'text':request['text'],'request_id':output.request_id,'source':source})
                return 'Automation trigger accepted. Action completion is tracked by HA automation traces.'
            except Exception:
                return 'Dispatch outcome unknown; not retried.'
        result=await output.tool('run_phrase_automation',{'automation_id':rule['id']},dispatch)
        record['reply']=rule['reply'] if result.startswith('Automation trigger accepted.') else 'I couldn’t confirm that automation started. Please check before trying again.'
        return record

"""Ephemeral semantic HA presence. No GPS, history, inferred occupancy or writes."""
import asyncio
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import re
import time


def normalized(value):
    return re.sub(r'[^a-z0-9]+', ' ', str(value).lower()).strip()


@dataclass(frozen=True)
class PresenceSnapshot:
    person: str
    presence: str
    source: str
    observed_at: str
    ha_person: str = ''
    raw_zone: str = ''
    evidence_at: str = ''
    evidence_age_seconds: float | None = None
    resolution_status: str = 'unknown'
    reason: str = ''
    home_zone: str = ''
    office_zone: str = ''
    cached: bool = False
    cache_age_seconds: float = 0

    def prompt_context(self, text=''):
        result=f'{self.person} presence: {self.presence}'
        # Contextual cautions are ephemeral and contain no location internals.
        if re.search(r'\bhere\b',text,re.I):
            if self.presence=='HOME':
                result+='\nFor “here”, the configured home-room default may be used; HOME is not evidence of room occupancy.'
            else:
                result+='\n“Here” must not default to the home room. No non-home device or temperature source is configured. Ask which location or state that limitation; do not substitute home readings or act on home devices without an explicit home target.'
        if re.search(r'\b(?:at|in) (?:the )?office\b',text,re.I):
            result+='\nNo office device/temperature source is configured. Do not substitute home devices or readings.'
        return result

    def diagnostic(self):
        return asdict(self)


class PresenceProvider:
    def __init__(self, ha, household, cache_seconds=10, max_age_seconds=3600, timeout_seconds=3):
        self.ha=ha
        self.household=household
        self.cache_seconds=cache_seconds
        self.max_age_seconds=max_age_seconds
        self.timeout_seconds=timeout_seconds
        self._cache={}
        self._lock=asyncio.Lock()
        self.last_diagnostic={}

    async def get_presence(self, person=None, force=False):
        from dataclasses import replace
        person=person or self.household.get('primary_person','Karthik')
        async with self._lock:
            now=time.monotonic()
            prior=self._cache.get(person)
            if prior and not force:
                stamp,snapshot=prior
                age=now-stamp
                evidence_age=(snapshot.evidence_age_seconds or 0)+age
                if age<self.cache_seconds and snapshot.presence!='UNKNOWN' and evidence_age<=self.max_age_seconds:
                    snapshot=replace(snapshot,cached=True,cache_age_seconds=round(age,3),evidence_age_seconds=round(evidence_age,3))
                    self.last_diagnostic=snapshot.diagnostic()
                    return snapshot
            snapshot=await self._fetch(person)
            self._cache[person]=(time.monotonic(),snapshot)
            self.last_diagnostic=snapshot.diagnostic()
            return snapshot

    async def _fetch(self, person):
        observed=datetime.now(timezone.utc)
        base=dict(person=person,presence='UNKNOWN',source='home_assistant',observed_at=observed.isoformat())
        def unknown(reason):return PresenceSnapshot(**base,reason=reason)
        cfg=self.household.get('people',{}).get(normalized(person),{})
        zones=cfg.get('presence_zones',{})
        eid=cfg.get('ha_person','');home=zones.get('home','');office=zones.get('office','')
        base.update(ha_person=eid,home_zone=home,office_zone=office)
        if not eid.startswith('person.') or not home.startswith('zone.') or not office.startswith('zone.') or home==office:
            return unknown('identity_or_zone_configuration_unresolved')
        try:
            async with asyncio.timeout(self.timeout_seconds):
                # Reuse the authenticated HA integration; no second full-state cache.
                p,h,o=await asyncio.gather(*(self.ha.request('GET','/api/states/'+e) for e in (eid,home,office)))
        except Exception:
            return unknown('ha_evidence_unavailable')
        if any(not isinstance(s,dict) or s.get('entity_id')!=e for s,e in zip((p,h,o),(eid,home,office))):
            return unknown('identity_or_zone_entity_missing')
        raw=str(p.get('state','')).strip();base['raw_zone']=raw
        if normalized(raw) in ('','unknown','unavailable','none'):
            return unknown('person_evidence_unavailable')
        if any(str(s.get('state','')).lower() in ('','unknown','unavailable') for s in (h,o)):
            return unknown('zone_configuration_unavailable')
        timestamp=p.get('last_reported') or p.get('last_updated')
        try:
            evidence=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
            if evidence.tzinfo is None:raise ValueError('timezone missing')
            age=(observed-evidence).total_seconds()
        except (TypeError,ValueError,AttributeError):
            return unknown('evidence_timestamp_missing_or_invalid')
        base.update(evidence_at=evidence.isoformat(),evidence_age_seconds=round(max(0,age),3))
        if age < -60:return unknown('evidence_timestamp_in_future')
        if age>self.max_age_seconds:return unknown('evidence_stale')
        def names(state,eid):
            return {normalized(eid),normalized(eid.split('.',1)[1]),normalized(state.get('attributes',{}).get('friendly_name',''))}-{''}
        home_names=names(h,home)|{'home'}
        office_names=names(o,office)
        if home_names & office_names or 'not home' in office_names:
            return unknown('zone_semantics_ambiguous')
        state=normalized(raw)
        semantic=self.map_zone(raw,home_names,office_names)
        base.update(presence=semantic,resolution_status='resolved')
        return PresenceSnapshot(**base)

    @staticmethod
    def map_zone(raw,home_names,office_names):
        state=normalized(raw)
        if state in ('','unknown','unavailable','none'):return 'UNKNOWN'
        return 'HOME' if state in home_names else 'OFFICE' if state in office_names else 'AWAY'

    async def history_identity(self,person=None):
        person=person or self.household.get('primary_person','Karthik')
        cfg=self.household.get('people',{}).get(normalized(person),{});zones=cfg.get('presence_zones',{})
        eid=cfg.get('ha_person','');home=zones.get('home','');office=zones.get('office','')
        if not eid.startswith('person.') or not home.startswith('zone.') or not office.startswith('zone.') or home==office:raise ValueError('Presence identity/zone configuration unavailable')
        async with asyncio.timeout(self.timeout_seconds):h,o=await asyncio.gather(*(self.ha.request('GET','/api/states/'+e) for e in (home,office)))
        def names(row,eid):return {normalized(eid),normalized(eid.split('.',1)[1]),normalized(row.get('attributes',{}).get('friendly_name',''))}-{''}
        if any(row.get('entity_id')!=expected or row.get('state') in ('unknown','unavailable') for row,expected in ((h,home),(o,office))):raise ValueError('Presence zones unavailable')
        a=names(h,home)|{'home'};b=names(o,office)
        if a & b or 'not home' in b:raise ValueError('Ambiguous zone semantics')
        return {'entity_id':eid,'home':a,'office':b}


def contextual_text(text,snapshot):
    """Resolve only contextual language. Explicit home targets stay remotely usable."""
    explicit_home=bool(re.search(r'\bat (?:the )?home\b|\b(?:in|at) (?:my|our|this) room\b',text,re.I))
    if re.search(r'\b(?:at|in) (?:the )?office\b',text,re.I):
        raise ValueError('presence context: office device mapping unavailable')
    if re.search(r'\bhere\b',text,re.I):
        if not explicit_home and (snapshot is None or snapshot.presence!='HOME'):
            raise ValueError('presence context: here is not established as home')
        text=re.sub(r'\bhere\b','',text,flags=re.I)
    text=re.sub(r'\bat (?:the )?home\b','',text,flags=re.I)
    # A bounded local temperature query uses the established home default.
    text=re.sub(r"((?:what's|what is) (?:the )?)temperature\s*([?.!]*)$",r'\1room temperature\2',text,flags=re.I)
    return re.sub(r'\s+',' ',text).strip()

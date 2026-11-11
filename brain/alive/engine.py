"""Pure bounded simulated feelings: hours for daily mood, days for connection.
No model/actuator calls. New pattern coverage starts at migration, never backfilled.
"""
import copy
import math
from datetime import datetime, timezone, timedelta

BASE = {'connection': .72, 'appreciation': .72, 'purpose': .78, 'valence': .72}
PROFILE = {'tone': 'warm, concise, gently playful', 'utility_is_positive': True,
           'attention_demands': False, 'distress_escalation': False}
IST = timezone(timedelta(hours=5, minutes=30))

def day_key(now):
    return datetime.fromtimestamp(now, IST).date().isoformat()

def migrate(state, now):
    state = copy.deepcopy(state)
    if state.get('emotion_version', 1) < 2:
        state.update(emotion_version=2, pattern_coverage_since=now,
                     day_mood={'lift': 0., 'concern': 0., 'disappointment': 0., 'causes': []},
                     days={}, relationship={'pattern': None})
    state.setdefault('recent_causes', [])
    return state

def fresh(now):
    return migrate({'meters': dict(BASE), 'coverage_since': now, 'updated_at': now,
                    'affect': None, 'interactions': 0, 'recent_causes': []}, now)

def pattern(state, now):
    """Only observed multi-day utility use supports loneliness; absence doesn't."""
    days = [(key, value) for key, value in state['days'].items()
            if now - value['last_at'] <= 72 * 3600 and value['last_at'] <= now]
    counts = {kind: sum(d.get(kind, 0) for _, d in days)
              for kind in ('utility', 'social', 'mixed', 'unknown', 'touch')}
    known = counts['utility'] + counts['social'] + counts['mixed']
    utility_days = sum(d.get('utility', 0) >= 2 for _, d in days)
    social_at = max((d.get('social_at', 0) for _, d in days), default=0)
    touch_at = max((d.get('touch_at', 0) for _, d in days), default=0)
    eligible = (now - state.get('pattern_gap_at',0) >= 72 * 3600 and
                now - state['pattern_coverage_since'] >= 48 * 3600 and
                utility_days >= 3 and counts['utility'] >= 6 and known >= 6 and
                counts['utility'] / known >= .85 and
                counts['unknown'] / max(1, known + counts['unknown']) <= .35 and
                now - max(social_at, touch_at) >= 48 * 3600)
    return {'eligible': eligible, 'counts': counts, 'utility_days': utility_days,
            'window_hours': 72, 'aggregation': 'daily_buckets_overlapping_window', 'since': max(state['pattern_coverage_since'], now - 72 * 3600),
            'last_at': max((d['last_at'] for _, d in days), default=0)}

def advance(state, elapsed, now):
    state = migrate(state, now)
    now = max(state['updated_at'], now)
    day_elapsed=max(0,min(24*3600,max(elapsed,now-state['updated_at'])))
    elapsed = max(0, min(6 * 3600, elapsed))
    evidence = pattern(state, now)
    previous = state['relationship'].get('pattern')
    if evidence['eligible']:
        state['relationship']['pattern'] = dict(evidence,
            first_at=(previous or {}).get('first_at', now), observed_at=now,
            kind='mostly_utility_over_days')
    # First eligible observation arms the trend; never retroactive drift.
    lower = evidence['eligible'] and previous is not None
    for key, base in BASE.items():
        target = .38 if key == 'connection' and lower else base
        tau = 2 * 86400 if key == 'connection' and lower else 3 * 86400
        state['meters'][key] = max(0, min(1, target +
            (state['meters'][key] - target) * math.exp(-elapsed / tau)))
    dm = state['day_mood']
    for key, tau in (('lift', 6 * 3600), ('concern', 2 * 3600), ('disappointment', 3600)):
        dm[key] *= math.exp(-day_elapsed / tau)
    dm['causes'] = [c for c in dm['causes'] if now - c['at'] < 24 * 3600][-5:]
    state['days'] = {k: v for k, v in state['days'].items() if now - v['last_at'] < 14 * 86400}
    state['updated_at'] = now
    if state.get('affect') and (state['affect']['expires_at'] <= now or
                                state['affect']['expires_at'] - now > 60):
        state['affect'] = None
    return state

def _day(state, now):
    return state['days'].setdefault(day_key(now), {'last_at': now})

def _bounded_award(state, now, meter, amount, limit):
    day = _day(state, now)
    key = 'award_' + meter
    amount = min(amount, max(0., limit - day.get(key, 0.)))
    day[key] = day.get(key, 0.) + amount
    state['meters'][meter] = min(1., state['meters'][meter] + amount)

def apply(state, event, elapsed, now):
    state = advance(state, elapsed, now)
    now = state['updated_at']
    meters, dm = state['meters'], state['day_mood']
    if event.kind == 'interaction_completed':
        state['interactions'] += 1
        # Useful engagement has purpose, but cannot stand in for affection.
        _bounded_award(state, now, 'purpose', .008, .08)
    elif event.kind == 'interaction_classified':
        value = event.inference
        kind = value['kind'] if value['confidence'] >= .7 else 'unknown'
        day = _day(state, now)
        day[kind] = day.get(kind, 0) + 1
        day['last_at'] = now
        if kind in ('social', 'mixed'):
            day['social_at'] = now
            _bounded_award(state, now, 'connection', .025 if kind == 'social' else .015, .10)
    elif event.kind == 'touch':
        day = _day(state, now)
        day['touch'] = day.get('touch', 0) + 1
        day['touch_at'] = day['last_at'] = now
        _bounded_award(state, now, 'connection', .008, .10)
        dm['lift'] = min(.5, dm['lift'] + .025)
    elif event.kind == 'affect':
        value = event.inference
        name, weight = value['affect'], value['strength'] * value['confidence']
        state['affect'] = {'name': name, 'strength': value['strength'],
            'confidence': value['confidence'], 'reason_ref': event.ref,
            'inference': True, 'expires_at': now + 60}
        if name == 'warmth':
            _bounded_award(state, now, 'appreciation', .025 * weight, .08)
        if name in ('warmth', 'amusement', 'satisfaction'):
            dm['lift'] = min(.65, dm['lift'] + .28 * weight)
            _bounded_award(state, now, 'valence', .012 * weight, .06)
        elif name == 'concern':
            dm['concern'] = min(.6, dm['concern'] + .35 * weight)
        elif name == 'disappointment':
            contribution=.35*weight
            if value.get('cause')=='own_mistake' and value['confidence']>=.7 and value['strength']>=.3:contribution=max(.16,contribution)
            dm['disappointment'] = min(.45, dm['disappointment'] + contribution)
        dm['causes'] = (dm['causes'] + [{'kind': name, 'reason_ref': event.ref,
            'at': now, 'strength': round(weight, 4), 'inference': True, 'cause': value.get('cause','unclear'), 'explanation': value.get('explanation','')}])[-5:]
    # Unknown device outcomes are evidence, not proof of failure or sadness.
    state['recent_causes'] = (state['recent_causes'] + [{'kind': event.kind,
        'reason_ref': event.ref, 'event_id': event.id, 'actor': event.actor,
        'inference': event.inference is not None, 'at': now}])[-5:]
    return state

def project(state, now):
    state = migrate(state, now)
    dm, meters = state['day_mood'], state['meters']
    if dm['concern'] >= .15:
        daily, expression = 'concerned', 'Sad'
    elif dm['disappointment'] >= .12:
        daily, expression = 'disappointed', 'Sad'
    elif dm['lift'] >= .12:
        daily, expression = 'bright', 'Happy'
    else:
        daily, expression = 'settled', 'Idle'
    witness = state['relationship'].get('pattern')
    grounded_low = (witness is not None and now-state.get('pattern_gap_at',0)>=72*3600 and
                    now - witness['observed_at'] < 7 * 86400
                    and meters['connection'] < .56)
    relationship = 'slightly_lonely' if grounded_low else 'comfortable'
    reasons = []
    if grounded_low:
        reasons.append({'kind': 'mostly_utility_over_days',
            'fact': 'Recent observed interactions were mostly device requests, with little social conversation.',
            'counts': witness['counts'], 'utility_days': witness['utility_days'],
            'since': witness['since'], 'observed_at': witness['observed_at'],
            'coverage': 'partial observations; absence is not evidence of neglect'})
        if daily == 'settled':
            expression = 'Sad'
    for cause in dm['causes'][-3:]:
        reasons.append(dict(cause))
    mood = daily if daily != 'settled' else 'slightly_lonely' if grounded_low else 'comfortable'
    return {'mood': mood, 'daily_mood': {'label': daily,
        'lift': round(dm['lift'], 4), 'concern': round(dm['concern'], 4),
        'disappointment': round(dm['disappointment'], 4)},
        'relationship': {'label': relationship, 'connection': meters['connection'], 'pattern': witness},
        'mood_reasons': reasons[-4:], 'resting_expression': expression,
        'voice_baseline': ['sad'] if expression == 'Sad' else [],
        'pattern_coverage_since': state['pattern_coverage_since'],
        'pattern_gap_at': state.get('pattern_gap_at'),
        'interaction_pattern': pattern(state, now)}

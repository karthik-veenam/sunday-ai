"""Source-proven control intent and timing summaries; no room/device name rules."""
import calendar
import math
import statistics
from datetime import datetime
from home_controls import written_targets


def controller_links(home, entity_id):
    candidates = {}
    for program in getattr(home, 'programs', {}).values():
        source = program.get('source', {})
        if program.get('domain') != 'automation' or entity_id not in written_targets(source.get('actions', source.get('action', []))):
            continue
        # Only mandatory positive top-level state gates establish this relationship.
        conditions = source.get('conditions', source.get('condition', []))
        if isinstance(conditions, dict): conditions = [conditions]
        for condition in conditions:
            if not isinstance(condition, dict) or condition.get('condition') != 'state' or condition.get('state') != 'on':
                continue
            eid = condition.get('entity_id')
            if not isinstance(eid, str) or not eid.startswith('input_boolean.') or eid in written_targets(source):
                continue
            if eid not in home.entities: continue
            candidates.setdefault(eid, []).append(program['entity_id'])
    if len(candidates) != 1: return None
    eid, programs = next(iter(candidates.items()))
    return {'entity_id': eid, 'name': home.entities[eid]['name'], 'programs': sorted(set(programs)),
            'meaning': 'Mandatory enabled-state gate in the automation source; not physical runtime'}


def start_summary(starts):
    """Do not call a broad/multimodal distribution one common start window."""
    from .store import timing
    if not starts:
        return {'window': None, 'clusters': [], 'reason': 'No complete starts observed'}
    values = [d.hour*60+d.minute for d in starts]
    window = timing(values)
    # A common window needs independent dates, not many restarts on one date.
    if len({d.date() for d in starts}) >= 3 and window['width_minutes'] <= 180:
        return {'window': window, 'clusters': [], 'reason': None}
    # Form bounded circular clusters, preserving different usage times. Parameters
    # are generic presentation bounds, not a room-specific habitual schedule.
    ordered = sorted(zip(values, starts), key=lambda x: x[0])
    gaps = [(ordered[(i+1) % len(ordered)][0]-ordered[i][0]) % 1440 for i in range(len(ordered))]
    cut = max(range(len(gaps)), key=gaps.__getitem__)
    ordered = ordered[cut+1:] + ordered[:cut+1]
    groups = []; current = []; previous = None; first = None; unfolded = 0
    for minute, dt in ordered:
        if previous is None: unfolded = minute
        else: unfolded += (minute-previous) % 1440
        if current and (unfolded-first > 180 or (minute-previous) % 1440 > 90):
            groups.append(current); current = []; first = None
        if first is None: first = unfolded
        current.append((minute, dt)); previous = minute
    if current: groups.append(current)
    clusters = []
    for group in groups:
        dates = {dt.date() for _, dt in group}
        if len(dates) < 3: continue
        clusters.append({**timing([v for v, _ in group]), 'sessions': len(group), 'distinct_dates': len(dates),
                         'session_starts': [dt.timestamp() for _, dt in group]})
    clusters.sort(key=lambda x: -x['distinct_dates'])
    return {'window': None, 'clusters': clusters[:4],
            'reason': 'Starts vary; no single common start window' if window['width_minutes'] > 180 else 'Fewer than three independent start dates'}


def session_stats(items, tz, full_days):
    from .store import quantile
    durations = [(s['end']-s['start'])/60 for s in items]
    starts = [datetime.fromtimestamp(s['start'], tz) for s in items]
    summary = start_summary(starts)
    for cluster in summary['clusters']:
        members = set(cluster.pop('session_starts'))
        minutes = [(s['end']-s['start'])/60 for s in items if s['start'] in members]
        cluster.update(median_minutes=statistics.median(minutes), mean_minutes=statistics.mean(minutes))
    daily = {}
    for session in items:
        date = datetime.fromtimestamp(session['start'], tz).date().isoformat()
        previous = daily.get(date)
        if previous is None or (session['end']-session['start'], -session['start']) > (previous['end']-previous['start'], -previous['start']):
            daily[date] = session
    main_items = list(daily.values())
    main_starts = [datetime.fromtimestamp(s['start'], tz) for s in main_items]
    main_summary = start_summary(main_starts)
    main_window = main_summary['window']; supported = len(main_items)
    main_members = main_items
    # A dominant group can establish the main start without forcing occasional
    # early/late uses into its range. Report the support fraction explicitly.
    if main_window is None and main_summary['clusters']:
        dominant = main_summary['clusters'][0]
        if dominant['distinct_dates'] / len(main_items) >= .6:
            main_window = {k:dominant[k] for k in ('start_minutes','end_minutes','width_minutes','crosses_midnight')}
            stamps = set(dominant['session_starts']);main_members=[s for s in main_items if s['start'] in stamps]
            supported = len(main_members)
    median_start = None
    if main_window and main_members:
        values = [datetime.fromtimestamp(s['start'],tz).hour*60+datetime.fromtimestamp(s['start'],tz).minute for s in main_members]
        center = math.atan2(sum(math.sin(v*math.tau/1440) for v in values),sum(math.cos(v*math.tau/1440) for v in values))*1440/math.tau%1440
        median_start = round((center+statistics.median([(v-center+720)%1440-720 for v in values]))%1440,1)
    main_durations = [(s['end']-s['start'])/60 for s in main_items]
    main_daily = {'rule':'Longest complete session per local start date; overnight sessions remain intact',
                  'days':len(main_items),'supporting_days':supported if main_window else 0,
                  'window':main_window,'median_start_minutes':median_start,
                  'median_minutes':statistics.median(main_durations) if main_durations else None,
                  'mean_minutes':statistics.mean(main_durations) if main_durations else None,
                  'reason':None if main_window else 'Fewer than three complete start dates' if len(main_items)<3 else 'Daily main starts still vary; no dominant start group',
                  'session_ids':[s.get('id') for s in main_items]}
    weekdays = []
    for n in range(7):
        indices = [i for i, dt in enumerate(starts) if dt.weekday() == n]
        opportunities = [date for date in full_days if datetime.fromisoformat(date).weekday() == n]
        occurrences = {starts[i].date().isoformat() for i in indices}
        used = len(occurrences.intersection(opportunities))
        weekdays.append({'day': calendar.day_name[n], 'sessions': len(indices), 'observed_days': len(opportunities),
                         'days_used': used, 'frequency': used/len(opportunities) if opportunities else None,
                         'strong_enough': used >= 3 and len(opportunities) >= 3,
                         'start_window': start_summary([starts[i] for i in indices])['window'],
                         'median_minutes': statistics.median([durations[i] for i in indices]) if indices else None})
    return {'complete_sessions': len(items), 'distinct_dates': len({s.date() for s in starts}),
            'median_minutes': statistics.median(durations) if durations else None,
            'mean_minutes': statistics.mean(durations) if durations else None,
            'typical_low_minutes': quantile(durations, .1), 'typical_high_minutes': quantile(durations, .9),
            'start_window': main_window, 'start_window_basis':'longest_complete_session_per_local_start_date',
            'main_daily':main_daily,'all_session_start_window':summary['window'],
            'start_clusters': summary['clusters'], 'start_reason': main_daily['reason'],
            'weekday': weekdays, 'short_intervals': sum(d < .5 for d in durations)}

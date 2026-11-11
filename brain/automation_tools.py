"""Create native HA automations; HA owns validation, persistence and scheduling."""
import asyncio
import hashlib
import json
import re

from home_tools import ALLOWED

DEFINITIONS = [{
    'name': 'create_automation',
    'description': 'Create and enable a persistent Home Assistant automation when the user asks for recurring/scheduled or event-driven behavior. Resolve exact entities first. Supply native HA triggers, optional conditions and actions. Time triggers use HA local timezone, 24-hour HH:MM:SS. Empty conditions means every day. Supports time/state/numeric_state/sun/event/zone/homeassistant triggers and existing household services, delays, choose, if and bounded repeats. HA validates before saving. Saves only; never executes actions now. Identical trigger/condition/action plans reuse the same automation rather than duplicating it. Never claim active unless active=true. Editing existing automation definitions is not supported by this tool; enabling/disabling existing ones uses home_action.',
    'input_schema': {'type': 'object', 'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        'description': {'type': 'string', 'maxLength': 1000},
        'triggers': {'type': 'array', 'items': {'type': 'object'}, 'minItems': 1, 'maxItems': 12},
        'conditions': {'type': 'array', 'items': {'type': 'object'}, 'maxItems': 20},
        'actions': {'type': 'array', 'items': {'type': 'object'}, 'minItems': 1, 'maxItems': 30},
        'mode': {'type': 'string', 'enum': ['single', 'restart', 'queued', 'parallel']},
    }, 'required': ['name', 'triggers', 'actions']},
}]
_lock = asyncio.Lock()


def plan(inputs, known_entities, services):
    """Validate references without executing any action; HA validates native syntax."""
    config = {'alias': inputs['name'], 'description': inputs.get('description', ''),
              'triggers': inputs['triggers'], 'conditions': inputs.get('conditions', []),
              'actions': inputs['actions'], 'mode': inputs.get('mode', 'single')}
    encoded = json.dumps(config, sort_keys=True)
    if len(encoded) > 24000 or any(x in encoded for x in ('{{', '{%', '{#')):
        raise ValueError('Use a bounded explicit automation plan, with resolved entities and services rather than templates.')

    def references(value):
        if isinstance(value, dict):
            if any(k in value for k in ('device_id', 'area_id', 'floor_id', 'label_id')):
                raise ValueError('Resolve explicit entity_id targets before creating the automation.')
            for key, item in value.items():
                if key == 'entity_id':
                    ids = [item] if isinstance(item, str) else item
                    if not isinstance(ids, list) or not ids or any(e not in known_entities for e in ids):
                        raise ValueError('Automation references an unknown entity.')
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)
    references(config)
    for trigger in config['triggers']:
        if trigger.get('trigger', trigger.get('platform')) not in {'time', 'state', 'numeric_state', 'sun', 'event', 'zone', 'homeassistant'}:
            raise ValueError('Unsupported trigger type for Sunday creation.')

    count = 0
    def steps(items, depth=0):
        nonlocal count
        if not isinstance(items, list) or depth > 5:
            raise ValueError('Invalid or overly nested action sequence.')
        for item in items:
            count += 1
            if not isinstance(item, dict) or count > 60:
                raise ValueError('Too many automation actions.')
            service = item.get('action', item.get('service'))
            if service is not None:
                if not isinstance(service, str) or not re.fullmatch(r'[a-z_]+\.[a-z_0-9]+', service):
                    raise ValueError('Specify an explicit household service.')
                domain, action = service.split('.')
                if action not in ALLOWED.get(domain, set()) or service not in services:
                    raise ValueError('Unsupported household service: ' + service)
                if not item.get('target', {}).get('entity_id') and not item.get('data', {}).get('entity_id'):
                    raise ValueError('Each household action needs explicit entity_id targets.')
            elif 'delay' in item or 'condition' in item or 'stop' in item:
                pass
            elif 'choose' in item:
                for choice in item['choose']:
                    steps(choice['sequence'], depth+1)
                steps(item.get('default', []), depth+1)
            elif 'if' in item:
                steps(item['then'], depth+1); steps(item.get('else', []), depth+1)
            elif 'repeat' in item:
                repeat = item['repeat']
                if type(repeat.get('count')) is not int or not 1 <= repeat['count'] <= 100:
                    raise ValueError('Repeats need a bounded count from 1 to 100.')
                steps(repeat['sequence'], depth+1)
            else:
                raise ValueError('Unsupported action sequence step.')
    steps(config['actions'])
    identity = {k: config[k] for k in ('triggers', 'conditions', 'actions', 'mode')}
    automation_id = 'sunday_' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    return automation_id, config


async def create(ha, inputs):
    from output import current_output
    output = current_output.get()
    if not ha.actions_enabled or (output and output.read_only):
        return {'ok': False, 'error': 'Automation creation is disabled for this turn.'}
    async with _lock:
        try:
            states, available, settings = await asyncio.gather(ha.get_states(), ha.request('GET', '/api/services'), ha.request('GET', '/api/config'))
            known = {s['entity_id'] for s in states}
            services = {d['domain']+'.'+s for d in available for s in d['services']}
            aid, config = plan(inputs, known, services)
            path = '/api/config/automation/config/' + aid
            # HA config GET raises on a missing ID. Distinguish 404/400 from network failures.
            existing = None
            try:
                existing = await ha.request('GET', path)
            except RuntimeError as error:
                if not any(code in str(error) for code in ('HTTP 404', 'HTTP 400')):
                    raise
            if existing is not None:
                for key in ('triggers', 'conditions', 'actions', 'mode'):
                    if existing.get(key) != config[key]:
                        return {'ok': False, 'error': 'Existing automation was edited in HA; it has not been overwritten.', 'automation_id': aid}
            write_error = None
            if existing is None:
                try:
                    await ha.request('POST', path, config)
                except Exception as error:
                    write_error = type(error).__name__
            # Readback reconciles an ambiguous write; never blindly repeat POST.
            try:
                saved = await ha.request('GET', path)
            except Exception:
                return {'ok': False, 'saved': None, 'automation_id': aid, 'error': 'Could not confirm automation persistence. Do not retry creation blindly.', 'write_error': write_error}
            if any(saved.get(k) != config[k] for k in ('triggers', 'conditions', 'actions', 'mode')):
                return {'ok': False, 'automation_id': aid, 'error': 'Saved automation differs from requested configuration.'}
            entity = None
            for _ in range(16):
                entity = next((s for s in await ha.get_states() if s['entity_id'].startswith('automation.') and str(s.get('attributes', {}).get('id')) == aid), None)
                if entity:
                    break
                await asyncio.sleep(.5)
            return {'ok': True, 'saved': True, 'created': existing is None, 'already_exists': existing is not None,
                    'active': bool(entity and entity['state'] == 'on'), 'automation_id': aid,
                    'entity_id': entity['entity_id'] if entity else None, 'name': saved.get('alias'),
                    'timezone': settings.get('time_zone'), 'configuration': saved,
                    'actions_executed_now': False, 'edit_in': 'Home Assistant Automations'}
        except (ValueError, KeyError, TypeError) as error:
            return {'ok': False, 'error': str(error)}
        except Exception as error:
            return {'ok': False, 'error': 'Automation creation could not be confirmed: '+type(error).__name__}

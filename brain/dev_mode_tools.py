"""Dev mode for Sunday: open the Codex dev-mode page and route the voice to Codex until told to stop.

Talks to the dev-mode bridge on this Pi (loopback, token from its token file). Two entry points, like camera_tools:
  * DEFINITIONS / execute(inputs) -> one tool the LLM can call when Karthik wants to work on Sunday with Codex.
  * attempt(request, output)      -> fast (no-LLM) path for the plain phrases.
"""
import re
import aiohttp
from pathlib import Path

import asyncio
import logging

BRIDGE = 'http://127.0.0.1:8775'
PROJECTOR = 'switch.16amp_smart_plug_2_socket_1'
BOOT_WAIT = 60        # the projector needs about a minute to boot before the Fire TV picture shows
READY_WAIT = 0        # projector was already on
log = logging.getLogger('dev_mode')
_display_task = None
TOKEN_FILE = Path('/home/djpi/sunday-dev-mode/token')

DEFINITIONS = [{
    'name': 'dev_mode',
    'description': ("Dev mode: a projector page where Karthik talks to Codex (the coding agent) and watches it work on Sunday. "
                    "action 'open' turns dev mode on: the mic listens continuously and everything Karthik says goes to Codex, not to you, "
                    "until he says 'end dev mode'. Use it when he wants to build, fix, change or look into Sunday's code, "
                    "or asks for dev mode or Codex. 'close' ends it. 'pause_listening' / 'resume_listening' mute or unmute the dev-mode mic "
                    "while it stays open. 'status' reports the state. Only 'open' when he actually asks to work on Sunday; "
                    "never open it for a general question."),
    'input_schema': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': ['open', 'close', 'pause_listening', 'resume_listening', 'status']}},
        'required': ['action'], 'additionalProperties': False},
}]

_ACTIONS = {'open': ('POST', '/api/devmode/start', None), 'close': ('POST', '/api/devmode/stop', None),
            'pause_listening': ('POST', '/api/devmode/listen', {'on': False}),
            'resume_listening': ('POST', '/api/devmode/listen', {'on': True}), 'status': ('GET', '/api/devmode', None)}


async def _call(method, path, body=None, timeout=8):
    try:
        token = TOKEN_FILE.read_text().strip()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.request(method, BRIDGE + path, params={'t': token}, json=body) as r:
                if r.status != 200:
                    return None
                return await r.json()
    except Exception:
        return None


async def projector_state():
    import tools
    if not tools._ha:
        return 'unknown'
    try:
        return (await tools._ha.get_state(PROJECTOR)).get('state', 'unknown')
    except Exception:
        return 'unknown'


async def bring_up_display(known_state=None):
    """Navigate immediately when already powered; cold-boot only a confirmed off projector."""
    try:
        import tools
        state = known_state if known_state is not None else await projector_state()
        wake = state == 'off'
        if wake:
            result = await tools._ha.call_service('switch', 'turn_on', {'entity_id': PROJECTOR})
            if result != 'ok':
                log.warning('Projector power-on was not confirmed')
                return
            await asyncio.sleep(BOOT_WAIT)
        # Unknown power state is not a reason to send another power command.
        await _call('POST', '/api/devmode/display', {'wake': wake}, timeout=40)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning('bring_up_display failed: %s', e)


async def _run(action):
    """Call the bridge; on 'open' also start the display bring-up, on 'close' cancel a pending one."""
    global _display_task
    method, path, body = _ACTIONS[action]
    state = await _call(method, path, body)
    if state is not None:
        if action == 'open':
            if _display_task and not _display_task.done():
                _display_task.cancel()
            power = await projector_state()
            state['projector_state'] = power
            _display_task = asyncio.create_task(bring_up_display(power))
        elif action == 'close' and _display_task and not _display_task.done():
            _display_task.cancel()
    return state


async def execute(inputs):
    state = await _run(inputs['action'])
    if state is None:
        return "Dev mode isn't reachable right now."
    if not state['on']:
        return 'Dev mode is off.'
    return 'Dev mode is on and listening.' if state['listening'] else 'Dev mode is on but the mic is paused.'


_OPEN = re.compile(r"^(?:(?:open|start|enter|begin|launch|turn on|switch to|go (?:in)?to|activate)\s+)(?:your\s+)?dev(?:elopment)?\s*mode$|^dev(?:elopment)?\s*mode(?:\s+on)?$")
_CLOSE = re.compile(r"^(?:end|exit|stop|close|quit|leave|turn off)\s+dev(?:elopment)?\s*mode$")
_RESUME = re.compile(r"^(?:resume|start|continue|keep)\s+listening$|^listen(?:\s+again)?$")


def _clean(text):
    t = (text or '').lower().strip().rstrip('.!?, ').strip()
    t = re.sub(r"^(?:hey sunday[, ]+|sunday[, ]+)", '', t)
    t = re.sub(r"^(?:can|could|would|will) you\s+(?:please\s+)?", '', t)
    t = re.sub(r"^please\s+", '', t)
    return re.sub(r'\s+', ' ', t.replace(',', ' ')).strip()


async def attempt(request, output):
    """Fast-path hook for the runtime: a handled record or None."""
    if request.get('source') != 'voice' or getattr(output, 'read_only', False):
        return None
    text = request.get('text', '')
    t = _clean(text)
    if _OPEN.match(t):
        action, ok_reply = 'open', "Dev mode is on. Turning on the projector. I'll be listening, and everything you say goes to Codex."
    elif _CLOSE.match(t):
        action, ok_reply = 'close', 'Dev mode is off.'
    elif _RESUME.match(t):
        state = await _call('GET', '/api/devmode')
        if not state or not state['on'] or state['listening']:
            return None
        action, ok_reply = 'resume_listening', "Listening again."
    else:
        return None
    state = await _run(action)
    if action == 'open' and state is not None and state.get('projector_state') != 'off':
        ok_reply = "Dev mode is on. Opening it on the projector. Everything you say goes to Codex."
    reply = ok_reply if state is not None else "Dev mode isn't reachable right now."
    return {'id': output.request_id, 'channel': 'voice', 'input': text, 'mode': 'execute', 'plan': [], 'reason': 'Dev mode fast path',
            'handled': True, 'decision': 'DEV_MODE', 'text': text, 'request_id': output.request_id, 'source': 'voice', 'reply': reply}

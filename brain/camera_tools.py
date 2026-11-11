"""Sunday's camera: the Stack-chan body takes a photo and shows it on its own screen. Nothing is saved or sent anywhere.

The body is reached through the local Stack-chan audio gateway (loopback). When the original AIRHUG audio backend is active the
gateway is not running and the camera is simply reported unavailable.

If the room is dark (the body's ambient light sensor reads under DARK_LUX), the room lights (light 1, light 2, cove, spots) that are off are switched on for the photo and
switched off again once the picture has been shown. A light that was already on is never touched.
"""
import asyncio
import os
import re
import aiohttp

GATEWAY = 'http://127.0.0.1:8773'
GATEWAY_URL = GATEWAY + '/camera/capture'
LIGHT_URL = GATEWAY + '/light'

DARK_LUX = float(os.environ.get('SUNDAY_CAMERA_DARK_LUX', '10'))   # below this the room counts as dark (the body's sensor reads low behind its shell)
LIGHT_SETTLE_S = 1.5                                               # let the light come up before the shot
LIGHT_HOLD_S = 11.0                                                # keep it on until the photo has been shown, then restore

_tasks = set()

DEFINITIONS = [{
    'name': 'take_picture',
    'description': ("Take a photo with Sunday's own camera (the Stack-chan body with the animated face). The photo is shown on the body's "
                    "screen for a few seconds; it is not saved or sent anywhere, and you cannot see or describe it. If the room is dark "
                    "the lights that are off are turned on automatically for the photo and off again afterwards. Use whenever Karthik asks you to "
                    "take, click or snap a picture or photo. Works only while the body is connected."),
    'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
}]


async def capture():
    """Ask the body to take a photo. Returns (ok, detail)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.post(GATEWAY_URL) as response:
                data = await response.json(content_type=None)
                return bool(data.get('ok')), str(data.get('detail', ''))
    except Exception as error:
        return False, 'gateway unreachable (' + type(error).__name__ + ')'


async def room_lux():
    """Brightness of the room in lux from the body's light sensor, or None if it cannot be read."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
            async with session.get(LIGHT_URL) as response:
                data = await response.json(content_type=None)
                return float(data['lux']) if data.get('ok') and 'lux' in data else None
    except Exception:
        return None


def _ha_client():
    import tools
    return getattr(tools, '_ha', None)


# "All lights" for a photo: the room's main lights, the same four Karthik means when he says it. (Not the foot lamp, moon, top light, panels or dashboard.)
PHOTO_LIGHTS = ('light 1', 'light 2', 'cove', 'spots')


def _light_names():
    return list(PHOTO_LIGHTS)


async def light_up_if_dark():
    """If the room is dark, switch on the room lights that are off (PHOTO_LIGHTS). Returns (client, [names we switched on]) or None if nothing was done."""
    lux = await room_lux()
    if lux is None or lux >= DARK_LUX:
        return None
    import ha
    client = _ha_client()
    if client is None:
        return None
    try:
        off = []
        for name in _light_names():
            state = await client.get_state(ha.resolve(name))
            if state.get('state') == 'off':               # lights that are on (or unavailable) are left alone
                off.append(name)
        if not off:
            return None
        results = await asyncio.gather(*(ha.control(client, name, 'on') for name in off))
    except Exception:
        return None
    turned = [name for name, result in zip(off, results) if str(result).strip().lower().endswith(' on.')]
    if not turned:
        return None
    await asyncio.sleep(LIGHT_SETTLE_S)
    return client, turned


async def _restore(client, names):
    try:
        await asyncio.sleep(LIGHT_HOLD_S)
        import ha
        await asyncio.gather(*(ha.control(client, name, 'off') for name in names))
    except Exception:
        pass


async def take_photo():
    """Light the room if it is dark, take the photo, put the light back. Returns (ok, detail, lit)."""
    lit = await light_up_if_dark()
    ok, detail = await capture()
    if lit is not None:
        task = asyncio.ensure_future(_restore(*lit))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return ok, detail, lit is not None


async def execute(inputs=None):
    ok, detail, lit = await take_photo()
    if ok:
        extra = ' It was dark, so I turned on the lights for it; they go off again afterwards.' if lit else ''
        return 'The photo is being taken and will appear on the screen of my body in a moment.' + extra
    return 'The camera is not available right now: ' + detail + '.'


# ---- no-LLM fast path -------------------------------------------------------------------------------------------------------
# Deliberately strict: only a plain "take a picture" style command. Anything with negation, conditions or extra clauses
# ("don't take a picture", "take a picture and send it", "why can't you take a picture") goes to the LLM, which has the tool.
_BLOCK = re.compile(r"\b(?:not|don'?t|dont|never|no|stop|why|how|if|when|unless|because|or|and|then|can'?t|cannot|able)\b")
_COMMAND = re.compile(
    r"(?:(?:can|could|would|will) you )?(?:please )?(?:click|take|snap|capture|shoot)(?: me)?(?: (?:a|an|one|another|the))?"
    r"(?: (?:quick|nice|good|new|funny))?(?: (?:picture|photo|photograph|pic|selfie))(?: of (?:me|us|this|that|it))?(?: please)?"
    r"|(?:please )?say cheese|smile for the camera")


def _clean(text):
    t = (text or '').lower().strip().rstrip('.!?, ').strip()
    t = re.sub(r"^(?:hey sunday[, ]+|sunday[, ]+)", '', t)
    t = re.sub(r"[,]", ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def is_camera_command(text):
    t = _clean(text)
    return bool(t) and not _BLOCK.search(t) and bool(_COMMAND.fullmatch(t))


async def attempt(request, output):
    """Fast-path hook for the runtime: returns a handled record (same shape as the phrase-automation one) or None."""
    source = request.get('source')
    if source != 'voice' or getattr(output, 'read_only', False):
        return None
    text = request.get('text', '')
    if not is_camera_command(text):
        return None
    ok, _detail, lit = await take_photo()
    if not ok:
        reply = "My camera isn't available right now."
    elif lit:
        reply = 'It was dark, so I switched on the lights. Say cheese!'
    else:
        reply = 'Say cheese!'
    return {'id': output.request_id, 'channel': source, 'input': text, 'mode': 'execute', 'plan': [], 'reason': 'Camera fast path',
            'handled': True, 'decision': 'CAMERA', 'text': text, 'request_id': output.request_id, 'source': source, 'reply': reply}

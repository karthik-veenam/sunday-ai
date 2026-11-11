"""Fast (no-LLM) voice commands for Sunday's own body: speaker VOLUME and BATTERY.

    "speak louder" / "increase your volume"       "set your volume to 60 percent"       "what's your volume?"
    "speak quieter" / "turn down your volume"     "what's your battery?"                "are you charging?"

Deliberately narrow: a phrase is only handled here when it is clearly about HER ("your volume", "body volume", "speak louder",
"your battery"). A bare "volume up" / "turn up the volume" / "battery" could mean the TV, the soundbar or the house battery
monitor, so those still go to the normal brain. Anything with negation, conditions or several requests also goes to the brain.

The body is reached through the local Stack-chan gateway (loopback). Volume is in PERCENT (the device uses 0..255); by voice it
never goes below 10 % so she can always be heard confirming (Home Assistant can still set anything).
"""
import re
import aiohttp

GATEWAY = 'http://127.0.0.1:8773'
STEP = 15            # "louder" / "quieter" = this many percentage points
FLOOR = 10           # lowest volume reachable by voice

_BLOCK = re.compile(r"\b(?:not|don'?t|dont|never|no|stop|if|when|unless|because|then|or|and|also|but)\b")

_NUMBERS = {'zero': 0, 'ten': 10, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80,
            'ninety': 90, 'hundred': 100, 'one hundred': 100, 'a hundred': 100}
_WORDS = {'max': 100, 'maximum': 100, 'full': 100, 'half': 50, 'minimum': FLOOR, 'lowest': FLOOR}

_ME = r"(?:your(?:\s+body'?s?)?|ur|the\s+body'?s?|body'?s?|sunday'?s?)"                 # "your volume", "body volume", "Sunday's volume"
_PLEASE = r"(?:\s+(?:a\s+(?:bit|little)|bit|little|more|please|for me))*"
_UP = (r"(?:(?:speak|talk|sound|be|get)\s+(?:a\s+(?:bit|little)\s+)?louder|speak\s+up"
       r"|(?:increase|raise|boost|turn\s+up|up|bring\s+up|crank\s+up)\s+" + _ME + r"\s+(?:speaker\s+)?volume"
       r"|" + _ME + r"\s+(?:speaker\s+)?volume\s+up)")
_DOWN = (r"(?:(?:speak|talk|sound|be|get)\s+(?:a\s+(?:bit|little)\s+)?(?:quieter|softer|lower)"
         r"|(?:decrease|lower|reduce|turn\s+down|bring\s+down|drop)\s+" + _ME + r"\s+(?:speaker\s+)?volume"
         r"|" + _ME + r"\s+(?:speaker\s+)?volume\s+down)")
_SET = (r"(?:(?:set|change|put|make|adjust)\s+)?" + _ME + r"\s+(?:speaker\s+)?volume\s+(?:to|at)\s+"
        r"(?P<val>\d{1,3}\s*(?:%|percent)?|(?:one\s+hundred|a\s+hundred|zero|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)(?:\s+percent)?"
        r"|max(?:imum)?|full|half|minimum|lowest)")
_BATTERY = (r"(?:(?:what(?:'s|\s+is)|how(?:'s|\s+is)|tell\s+me|check|show\s+me)\s+" + _ME + r"\s+battery(?:\s+(?:level|percentage|status|charge))?"
            r"|" + _ME + r"\s+battery(?:\s+(?:level|percentage|status|charge))?"
            r"|(?:what(?:'s|\s+is)|how\s+much)\s+(?:battery|charge)\s+(?:do\s+you\s+have|have\s+you\s+got|is\s+left\s+in\s+(?:you|your\s+body)|are\s+you\s+at)"
            r"|how\s+much\s+battery\s+(?:do\s+you\s+have|is\s+left)\s*(?:in\s+(?:you|your\s+body))?"
            r"|(?:are\s+you|you)\s+(?:charging|plugged\s+in)"
            r"|battery\s+level\s+of\s+(?:your\s+body|yourself)"
            r"|how(?:'s|\s+is)\s+(?:your\s+)?(?:power|charge))")


def _clean(text):
    t = (text or '').lower().strip().rstrip('.!?, ').strip()
    t = re.sub(r"^(?:hey sunday[, ]+|sunday[, ]+)", '', t)
    t = re.sub(r"^(?:can|could|would|will) you\s+(?:please\s+)?", '', t)
    t = re.sub(r"^please\s+", '', t)
    t = t.replace(',', ' ')
    return re.sub(r'\s+', ' ', t).strip()


def _percent(raw):
    raw = raw.strip()
    if raw in _WORDS:
        return _WORDS[raw]
    word = re.sub(r"\s*percent$", '', raw)
    if word in _NUMBERS:
        return _NUMBERS[word]
    m = re.match(r"(\d{1,3})", raw)
    return int(m.group(1)) if m else None


def parse(text):
    """-> ('volume_up'|'volume_down'|'volume_set'|'volume_get'|'battery', value) or None."""
    t = _clean(text)
    if not t or _BLOCK.search(t):
        return None
    if re.fullmatch(_UP + _PLEASE, t):
        return ('volume_up', STEP)
    if re.fullmatch(_DOWN + _PLEASE, t):
        return ('volume_down', STEP)
    m = re.fullmatch(_SET + r"(?:\s+please)?", t)
    if m:
        v = _percent(m.group('val'))
        return ('volume_set', v) if v is not None and 0 <= v <= 100 else None
    if re.fullmatch(r"(?:what(?:'s|\s+is)|tell\s+me)\s+" + _ME + r"\s+(?:speaker\s+)?volume(?:\s+(?:now|at))?", t) or re.fullmatch(r"how\s+loud\s+are\s+you", t):
        return ('volume_get', None)
    if re.fullmatch(_BATTERY, t):
        return ('battery', None)
    return None


def is_body_command(text):
    return parse(text) is not None


async def _call(method, path):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.request(method, GATEWAY + path) as response:
                return await response.json(content_type=None)
    except Exception as error:
        return {'ok': False, 'detail': 'gateway unreachable (' + type(error).__name__ + ')'}


def reply_for(kind, value, result):
    """The sentence Sunday says (spoken at the NEW volume, so a volume change is also heard)."""
    if not result.get('ok'):
        return "I can't reach my body right now." if kind != 'battery' else "I can't read my battery right now."
    if kind == 'battery':
        pct = result.get('percent')
        chg = result.get('charging')
        age = result.get('age_s', 0) or 0
        if pct is None:
            return "I can't read my battery right now."
        if age > 90:
            return "My last battery reading, about %d minutes ago, was %d percent." % (round(age / 60), pct)
        if chg == 1:
            return "I'm at %d percent and charging." % pct
        if pct <= 15:
            return "My battery is low, %d percent. Please plug me in." % pct
        return "My battery is at %d percent." % pct
    pct = result.get('percent')
    if kind == 'volume_get':
        return "My volume is at %d percent." % pct
    prev = result.get('previous')
    if kind == 'volume_up' and prev == pct == 100:
        return "I'm already at full volume."
    if result.get('limited') and pct == FLOOR and kind in ('volume_down', 'volume_set'):
        return "That's the lowest I'll go by voice, %d percent." % FLOOR
    if prev == pct:
        return "My volume is already %d percent." % pct
    return "Volume is now %d percent." % pct


async def attempt(request, output):
    """Fast-path hook for the runtime: a handled record (same shape as the camera / phrase-automation ones) or None."""
    source = request.get('source')
    if source != 'voice' or getattr(output, 'read_only', False):
        return None
    text = request.get('text', '')
    parsed = parse(text)
    if parsed is None:
        return None
    kind, value = parsed
    if kind == 'battery':
        result = await _call('GET', '/battery')
    elif kind == 'volume_get':
        result = await _call('GET', '/volume')
    elif kind == 'volume_set':
        result = await _call('POST', '/volume?set=%d' % value)
    else:
        result = await _call('POST', '/volume?delta=%d' % (value if kind == 'volume_up' else -value))
    return {'id': output.request_id, 'channel': source, 'input': text, 'mode': 'execute', 'plan': [], 'reason': 'Body volume/battery fast path',
            'handled': True, 'decision': 'BODY_COMMAND', 'text': text, 'request_id': output.request_id, 'source': source,
            'reply': reply_for(kind, value, result)}

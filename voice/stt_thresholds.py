"""Validated, hot command-listening settings. Does not configure wake detection."""
import fcntl
import json
import math
import os
import tempfile
from pathlib import Path

FILE = Path('/home/djpi/sunday-alive/speech_boundaries.json')
BASE_CONFIG = Path('/home/djpi/sunday/config.json')
LIMITS = {'stt_silence_threshold': (1, 32767, 1, 700),
          'stt_speech_threshold': (1, 32767, 1, 1200),
          'stt_silence_seconds': (0.1, 5.0, 0.1, 0.8)}

def validate(name, value):
    if name not in LIMITS or isinstance(value, bool):
        raise ValueError('Unknown setting or invalid value')
    lo, hi, step, _ = LIMITS[name]
    number = float(value)
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError('Listening value outside allowed range')
    if step == 1 and not number.is_integer():
        raise ValueError('RMS must be an integer')
    return int(number) if step == 1 else number

def read(fallback=None, path=None):
    values = {k: v[3] for k, v in LIMITS.items()}
    if fallback:
        values.update(fallback)
    elif path is None or path == FILE:
        try:
            base = json.loads(BASE_CONFIG.read_text())
            values.update({k: validate(k, base[k]) for k in LIMITS if k in base})
        except (OSError, ValueError, TypeError):
            pass
    try:
        data = json.loads((path or FILE).read_text())
        candidate = {k: validate(k, data.get(k, values[k])) for k in LIMITS}
        if candidate['stt_speech_threshold'] < candidate['stt_silence_threshold']:
            raise ValueError('Speech RMS must be at least silence RMS')
        return candidate
    except (OSError, ValueError, TypeError):
        return values

def save(name, value, path=None):
    path = path or FILE
    value = validate(name, value)
    with path.with_suffix(path.suffix + '.stt.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {}
        values = read(path=path)
        values[name] = value
        if values['stt_speech_threshold'] < values['stt_silence_threshold']:
            raise ValueError('Speech RMS must be at least silence RMS')
        data.update(values)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.stt-config-')
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(data, out, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, (path.stat().st_mode & 0o777) if path.exists() else 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

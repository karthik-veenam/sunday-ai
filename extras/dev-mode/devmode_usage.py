"""Allowlisted account quota projection, independent of model/token estimates."""
import time


def project_limits(result):
    bucket = (result.get('rateLimitsByLimitId') or {}).get('codex') or result.get('rateLimits') or {}
    windows = []
    for name in ('primary', 'secondary'):
        raw = bucket.get(name)
        if not isinstance(raw, dict):
            continue
        used, minutes, reset = raw.get('usedPercent'), raw.get('windowDurationMins'), raw.get('resetsAt')
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        label = {300: '5-hour', 1440: 'Daily', 10080: 'Weekly'}.get(minutes)
        if not label:
            label = f'{minutes // 60:g}-hour' if isinstance(minutes, (int, float)) and minutes % 60 == 0 else f'{minutes}-min' if minutes else name.capitalize()
        windows.append({'label': label, 'remainingPercent': max(0, min(100, 100-used)),
                        'windowDurationMins': minutes, 'resetsAt': reset if isinstance(reset, (int, float)) else None})
    return {'type': 'usage', 'windows': windows, 'updatedAt': time.time(),
            'available': bool(windows), 'stale': False}

"""Bounded recovery for the explicitly selected body route. No firmware writes."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

import sunday_audio as audio

ROOT = Path(audio.HOME)
STATE = ROOT / 'state/body_recovery.json'
USB = Path('/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_7C:4F:AD:AF:C9:C8-if00')
ESPTOOL = '/home/djpi/.sunday-pio/penv/bin/python'


def decision(state, health, now, usb_present):
    """Pure policy: preserve cooldowns across recovery and service restarts."""
    state = dict(state)
    state['resets'] = [t for t in state.get('resets', []) if 0 <= now-t < 3600]
    if not health:
        state.pop('offline_since', None)
        state['bad_health'] = state.get('bad_health', 0) + 1
        if state['bad_health'] >= 3 and now-state.get('gateway_restart', 0) >= 300:
            state.update(gateway_restart=now, bad_health=0)
            return state, 'restart_gateway'
        return state, 'waiting_gateway'
    state['bad_health'] = 0
    if health.get('device_connected'):
        state.pop('offline_since', None)
        return state, 'connected'
    since = state.setdefault('offline_since', now)
    if now < since:
        state['offline_since'] = now
        return state, 'waiting_body'
    if now-since < 180:
        return state, 'waiting_body'
    if not usb_present:
        return state, 'usb_missing'
    if health.get('playing') or health.get('face_state') in ('wake', 'listening', 'thinking', 'speaking'):
        return state, 'waiting_idle'
    if len(state['resets']) >= 3 or (state['resets'] and now-state['resets'][-1] < 600):
        return state, 'reset_cooldown'
    state['resets'].append(now)
    state['offline_since'] = now
    return state, 'reset_body'


def save(state):
    tmp = STATE.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(state, f); f.flush(); os.fsync(f.fileno())
    tmp.replace(STATE)


def main():
    ROOT.joinpath('state').mkdir(exist_ok=True)
    # The same lock as manual backend changes. Never fight an intentional switch.
    with open(audio.LOCK_FILE, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if audio.read_backend() != 'stackchan':
            # A later intentional switch starts a fresh offline grace period.
            if STATE.exists():
                state = json.loads(STATE.read_text()); state.pop('offline_since', None); save(state)
            return
        system = audio.RealSys()
        if not system.is_active(audio.GW_UNIT):
            system.start(audio.GW_UNIT)
        try:
            state = json.loads(STATE.read_text())
        except FileNotFoundError:
            state = {}
        health = system.health()
        previous_status = state.get('status')
        state, action = decision(state, health, time.time(), USB.exists())
        state['last_check'] = time.time(); state['status'] = action
        save(state)  # Reserve a retry before acting, including failed resets.
        if action == 'usb_missing' and previous_status != action:
            audio.log('recovery: body offline and registered USB device absent; physical power/cable check required')
        elif action == 'restart_gateway':
            audio.log('recovery: gateway failed three health checks; restarting gateway only')
            result = system._sc('restart', audio.GW_UNIT)
            if result.returncode:
                raise RuntimeError('Gateway restart failed')
        elif action == 'reset_body':
            audio.log('recovery: body offline for three minutes; USB reset of registered CoreS3')
            # Fixed by-id MAC, no enumeration, flash operation, stub or NVS writes.
            result = subprocess.run([
                ESPTOOL, '-m', 'esptool', '--chip', 'esp32s3', '--port', str(USB),
                '--no-stub', '--before', 'default-reset', '--after', 'hard-reset', 'chip-id',
            ], capture_output=True, text=True, timeout=25)
            state['reset_ok'] = result.returncode == 0; save(state)
            if result.returncode:
                raise RuntimeError('Body USB reset failed')


if __name__ == '__main__':
    main()

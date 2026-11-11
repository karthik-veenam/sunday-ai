"""Shared UI event bus and HA-backed presence, without legacy audio routes."""
import asyncio
import time

_clients = set()
_state_cache = {}
_presence_zone = {"phone": "unknown"}
_presence_last_seen = {"phone": 0.0}


async def emit(event):
    _state_cache[event.get("type", "event")] = event
    dead = []
    for client in list(_clients):
        try:
            await client.send_json(event)
        except Exception:
            dead.append(client)
    for client in dead:
        _clients.discard(client)


def update_presence(label, home, zone=None):
    _presence_last_seen[label] = time.time() if home else 0
    _presence_zone[label] = zone or ("home" if home else "not_home")


def get_presence():
    return {key: time.time() - value < 120 for key, value in _presence_last_seen.items()}


def get_presence_zone(label="phone"):
    return _presence_zone.get(label, "unknown")

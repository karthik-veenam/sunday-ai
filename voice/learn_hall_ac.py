import json
import time
import urllib.request

HA_URL = "http://localhost:8123"
SUNDAY_URL = "http://localhost:8080/announce"
SUNDAY_TOKEN = "REPLACE_ME"

with open("/home/djpi/sunday/config.json") as f:
    HA_TOKEN = json.load(f)["ha_token"]

REMOTE_ENTITY = "remote.hall_ir"
DEVICE = "hall_ac"
LEARN_TIMEOUT = 60

# (command_slug, instruction for the human)
COMMANDS = [
    ("off", "Turn the Hall AC OFF, then press the button on the remote that sends that OFF."),
    ("cool_auto_24", "Set Hall AC: Cool mode, Fan AUTO, 24°C — then press Send/confirm on the remote."),
    ("cool_auto_18", "Set Hall AC: Cool mode, Fan AUTO, 18°C — then press Send/confirm on the remote."),
    ("cool_auto_22", "Set Hall AC: Cool mode, Fan AUTO, 22°C — then press Send/confirm on the remote."),
    ("cool_auto_26", "Set Hall AC: Cool mode, Fan AUTO, 26°C — then press Send/confirm on the remote."),
    ("cool_auto_30", "Set Hall AC: Cool mode, Fan AUTO, 30°C — then press Send/confirm on the remote."),
    ("cool_low_24", "Set Hall AC: Cool mode, Fan LOW, 24°C — then press Send/confirm on the remote."),
    ("cool_mid_24", "Set Hall AC: Cool mode, Fan MID/MEDIUM, 24°C — then press Send/confirm on the remote."),
    ("cool_high_24", "Set Hall AC: Cool mode, Fan HIGH, 24°C — then press Send/confirm on the remote."),
]


def post_json(url, payload, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LEARN_TIMEOUT + 10) as r:
        return r.status, r.read().decode()


def announce(message):
    try:
        post_json(SUNDAY_URL, {"message": message, "channel": "telegram", "token": SUNDAY_TOKEN})
    except Exception as e:
        print(f"[announce error] {e}")


def learn_command(command):
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    payload = {
        "entity_id": REMOTE_ENTITY,
        "device": DEVICE,
        "command": command,
        "timeout": LEARN_TIMEOUT,
    }
    status, body = post_json(f"{HA_URL}/api/services/remote/learn_command", payload, headers)
    return status, body


def main():
    results = {}
    print(f"Starting learning session: {len(COMMANDS)} commands")
    for i, (slug, instruction) in enumerate(COMMANDS, 1):
        print(f"\n[{i}/{len(COMMANDS)}] {slug}: {instruction}")
        announce(f"AC learn {i}/{len(COMMANDS)}: {instruction} (you have {LEARN_TIMEOUT}s)")
        t0 = time.time()
        try:
            status, body = learn_command(slug)
            elapsed = time.time() - t0
            print(f"  -> HTTP {status} in {elapsed:.1f}s: {body[:200]}")
            results[slug] = {"status": status, "elapsed": elapsed}
        except Exception as e:
            print(f"  -> ERROR: {e}")
            results[slug] = {"error": str(e)}
        time.sleep(1.5)

    print("\n=== Summary ===")
    for slug, r in results.items():
        print(slug, r)

    with open("/home/djpi/sunday/hall_ac_learn_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()

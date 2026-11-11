import json
import time
import urllib.request

HA_URL = "http://localhost:8123"
SUNDAY_URL = "http://localhost:8080/announce"
SUNDAY_TOKEN = "REPLACE_ME"

with open("/home/djpi/sunday/config.json") as f:
    HA_TOKEN = json.load(f)["ha_token"]

REMOTE_ENTITY = "remote.hall_ir"
DEVICE = "hall_fan"
LEARN_TIMEOUT = 60

COMMANDS = [
    ("power", "Hall FAN: press the ON/OFF (power) button on the fan remote."),
    ("swing", "Hall FAN: press the SWING on/off button on the fan remote."),
    ("speed", "Hall FAN: press the SPEED increase button on the fan remote."),
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
    print(f"Starting fan learning session: {len(COMMANDS)} commands")
    for i, (slug, instruction) in enumerate(COMMANDS, 1):
        print(f"\n[{i}/{len(COMMANDS)}] {slug}: {instruction}")
        announce(f"Fan learn {i}/{len(COMMANDS)}: {instruction} (you have {LEARN_TIMEOUT}s)")
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

    with open("/home/djpi/sunday/hall_fan_learn_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()

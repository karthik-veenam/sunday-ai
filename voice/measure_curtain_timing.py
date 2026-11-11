import time, json, requests

with open("/home/djpi/sunday/config.json") as f:
    TOKEN = json.load(f)["ha_token"]

BASE = "http://localhost:8123"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
ENTITY = "cover.dining_dining_balcony"


def get_state():
    r = requests.get(f"{BASE}/api/states/{ENTITY}", headers=HEADERS, timeout=5)
    return r.json()["state"]


def wait_for_state(target, timeout=60):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        s = get_state()
        if s == target:
            return time.monotonic() - start
        time.sleep(0.25)
    return None


def call(service):
    requests.post(f"{BASE}/api/services/cover/{service}", headers=HEADERS, json={"entity_id": ENTITY}, timeout=5)


current = get_state()
print("starting state:", current)

if current != "closed":
    call("close_cover")
    t = wait_for_state("closed")
    print("close duration (from open):", t)
    time.sleep(1)

call("open_cover")
t = wait_for_state("open")
print("open duration:", t)
time.sleep(1)

call("close_cover")
t = wait_for_state("closed")
print("close duration:", t)
time.sleep(1)

call("open_cover")
t = wait_for_state("open")
print("open duration (2nd):", t)

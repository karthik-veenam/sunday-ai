#!/usr/bin/env python3
import urllib.request, urllib.parse, json, os, sys

URL = "https://in.bookmyshow.com/movies/hyd/seat-layout/ET00504055/ALUC/2907/20260722"
STATE_FILE = os.path.expanduser("~/sunday/bms_state.json")
LOG_FILE = os.path.expanduser("~/sunday/bms_monitor.log")
TELEGRAM_TOKEN = "REPLACE_ME"
TELEGRAM_CHAT_ID = "REPLACE_ME"

STATUS_MAP = {"0": "Sold Out", "1": "Almost Full", "2": "Filling Fast"}


def status_label(code):
    return STATUS_MAP.get(str(code), f"Available (code {code})")


def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{msg}\n")


def fetch_showtimes():
    req = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    key = "window.__INITIAL_STATE__"
    start = html.find(key)
    if start == -1:
        raise RuntimeError("INITIAL_STATE not found in page")
    eq = html.find("=", start) + 1
    i = eq
    while html[i] in " \t\n":
        i += 1
    depth = 0
    in_str = False
    esc = False
    j = i
    while True:
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
        j += 1
    data = json.loads(html[i:j])
    shows = data["seatlayoutMovies"]["seatLayoutData"]["currentVenue"]["ShowTimes"]
    result = {}
    for s in shows:
        showtime = s["ShowTime"]
        cats = {c["PriceDesc"]: str(c["AvailStatus"]) for c in s.get("Categories", [])}
        result[showtime] = cats
    return result


def send_telegram(text):
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=data
    )
    urllib.request.urlopen(req, timeout=10)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    try:
        current = fetch_showtimes()
    except Exception as e:
        log(f"fetch error: {e}")
        return

    last = load_state()

    for showtime, cats in current.items():
        last_cats = last.get(showtime, {})
        dc_now = cats.get("DIRECTOR CHOICE")
        dc_before = last_cats.get("DIRECTOR CHOICE")

        if dc_before is not None and dc_before == "0" and dc_now != "0":
            msg = (
                f"DIRECTOR CHOICE seats just opened for The Odyssey {showtime} "
                f"Dolby at ALLU Kokapet! Status: {status_label(dc_now)}. Book now!"
            )
            send_telegram(msg)
            log(f"ALERT (priority): {msg}")
        else:
            for cat_name, code in cats.items():
                if cat_name == "DIRECTOR CHOICE":
                    continue
                before_code = last_cats.get(cat_name)
                if before_code is not None and before_code == "0" and code != "0":
                    msg = (
                        f"BookMyShow: {cat_name} seats opened for The Odyssey {showtime} Dolby "
                        f"(Director's Choice still {status_label(dc_now)})."
                    )
                    send_telegram(msg)
                    log(f"ALERT (secondary): {msg}")

    last.update(current)
    save_state(last)
    log(f"checked ok: {json.dumps(current)}")


if __name__ == "__main__":
    main()

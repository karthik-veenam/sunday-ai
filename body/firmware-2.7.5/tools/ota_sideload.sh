#!/bin/bash
# Wireless SIDELOAD of a PREBUILT firmware image (no compiling, no cable): the quickest way to update the Stack-chan.
#
#   ./tools/ota_sideload.sh [firmware.bin] [device-ip]
#       firmware.bin  default: .pio/build/stackchan/firmware.bin   (must be the APP image, never *.factory.bin)
#       device-ip     default: $STACKCHAN_IP, else discovered from the Pi gateway's log, else 192.168.88.8 (the body's address changed from .17 to .8 on 2026-10-28)
#
# Needs: firmware >= 1.3 already running on the device (ArduinoOTA), the device on the same LAN, and the diag token
#        (env SUNDAY_DIAG_TOKEN, else read from src/secrets.h - never printed).
# How it works: espota sends a UDP invitation to port 3232, the device then connects BACK to this machine on a random TCP port
#        (10000-60000) and streams the image into the idle OTA slot, verifies it, flips otadata and reboots (~25 s).
#        If anything fails the running image is left untouched. See docs/OTA_SIDELOAD.md.
set -eu
cd "$(dirname "$0")/.."

BIN="${1:-.pio/build/stackchan/firmware.bin}"
IP="${2:-${STACKCHAN_IP:-}}"
if [ -z "$IP" ]; then   # the body gets its address by DHCP and it has changed before (.17 -> .8): ask the Pi's gateway where it connected from
  IP=$(ssh -o ConnectTimeout=5 -o BatchMode=yes djpi@192.168.88.108 "journalctl -u sunday-stackchan-gateway --no-pager -n 400 2>/dev/null | grep -o 'device connected from [0-9.]*' | tail -1 | awk '{print \$4}'" 2>/dev/null || true)
  [ -n "$IP" ] && echo "address from the gateway log: $IP" || IP=192.168.88.8
fi
MAX=$((0x640000))                                   # size of each OTA app slot in default_16MB.csv

die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$BIN" ] || die "image not found: $BIN"
case "$BIN" in *factory*) die "that is the factory (merged) image - OTA needs the plain app image (firmware.bin)";; esac
SIZE=$(wc -c < "$BIN" | tr -d ' ')
[ "$SIZE" -gt 100000 ] || die "image is only $SIZE bytes - not a real firmware"
[ "$SIZE" -le "$MAX" ] || die "image is $SIZE bytes, larger than the OTA slot ($MAX)"
[ "$(od -An -tx1 -N1 "$BIN" | tr -d ' \n')" = "e9" ] || die "not an ESP image (first byte should be 0xE9)"

TOKEN="${SUNDAY_DIAG_TOKEN:-$(sed -n 's/.*SUNDAY_DIAG_TOKEN "\([0-9a-f]*\)".*/\1/p' src/secrets.h 2>/dev/null || true)}"
[ -n "$TOKEN" ] || die "no diag token: set SUNDAY_DIAG_TOKEN or provide src/secrets.h"

# find espota.py: explicit env, bundled copy, or the PlatformIO Arduino package
ESPOTA="${ESPOTA:-}"
if [ -z "$ESPOTA" ]; then
  for c in ./ota/espota.py ./tools/espota.py \
           "${PLATFORMIO_CORE_DIR:-$HOME/.platformio}/packages/framework-arduinoespressif32/tools/espota.py" \
           /private/tmp/claude-501/-Users-karthik-veenam/34c4f79a-8db8-4faa-ba52-307d594d82d6/scratchpad/pio/packages/framework-arduinoespressif32/tools/espota.py; do
    [ -f "$c" ] && ESPOTA="$c" && break
  done
fi
[ -n "$ESPOTA" ] || die "espota.py not found (set ESPOTA=/path/to/espota.py; it ships in the Arduino-ESP32 package: tools/espota.py)"

echo "device : $IP"
curl -s -m 5 "http://$IP/status" -o /dev/null || die "device does not answer on http://$IP/status - is it on Wi-Fi? (try the cable: tools/flash_usb.sh)"
echo "image  : $BIN ($SIZE bytes, sha256 $(shasum -a 256 "$BIN" | cut -c1-16)...)"
BEFORE_UP=$(curl -s -m 5 "http://$IP/status" | sed -n 's/.*"uptime_s":\([0-9]*\).*/\1/p')

PROGRESS=""; [ -t 1 ] && PROGRESS="-r"            # the progress bar only makes sense in a terminal (it floods logs otherwise)
python3 "$ESPOTA" -i "$IP" -p 3232 -a "$TOKEN" -f "$BIN" $PROGRESS -t 20

echo "uploaded - waiting for the device to reboot..."
for i in $(seq 1 40); do
  sleep 2
  UP=$(curl -s -m 3 "http://$IP/status" | sed -n 's/.*"uptime_s":\([0-9]*\).*/\1/p' || true)
  if [ -n "$UP" ] && [ -n "$BEFORE_UP" ] && [ "$UP" -lt "$BEFORE_UP" ]; then
    echo "device is back (uptime ${UP}s < ${BEFORE_UP}s before): the new image is running."
    echo "verify the version: ssh djpi@192.168.88.108 'curl -s 127.0.0.1:8773/health'  (look at device.fw)"
    exit 0
  fi
done
die "the device did not come back with a reset uptime within 80 s - check it (and see docs/OTA_SIDELOAD.md troubleshooting)"

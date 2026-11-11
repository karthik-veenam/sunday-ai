#!/bin/bash
# Wireless firmware update (no cable). Builds the `stackchan_ota` env and uploads it over Wi-Fi to the Stack-chan (password = diag token).
#   ./tools/ota.sh            Needs the Stack-chan on the network at 192.168.88.17 (see platformio.ini). The token is read from src/secrets.h, never printed.
set -eu
cd "$(dirname "$0")/.."
export SUNDAY_DIAG_TOKEN="$(sed -n 's/.*SUNDAY_DIAG_TOKEN "\([0-9a-f]*\)".*/\1/p' src/secrets.h)"
[ -n "$SUNDAY_DIAG_TOKEN" ] || { echo "no SUNDAY_DIAG_TOKEN in src/secrets.h"; exit 1; }
SCRATCH=/private/tmp/claude-501/-Users-karthik-veenam/34c4f79a-8db8-4faa-ba52-307d594d82d6/scratchpad
if [ -d "$SCRATCH/pio" ]; then export PLATFORMIO_CORE_DIR="$SCRATCH/pio"; PIO="$SCRATCH/venv/bin/pio"; else export PLATFORMIO_CORE_DIR="${PLATFORMIO_CORE_DIR:-$HOME/.sunday-pio}"; PIO="${PIO:-pio}"; fi
rm -f "$PLATFORMIO_CORE_DIR/penv/.lock"
exec "$PIO" run -e stackchan_ota -t upload

#!/bin/bash
# USB flash of the app image (bootloader/partitions never change). Also rewrites boot_app0 (otadata) so the
# bootloader boots app0 even if an earlier wireless update left otadata pointing at app1. Use if wireless is unavailable. Close any serial monitor first.
#   ./tools/flash_usb.sh [port]      default port /dev/cu.usbmodem101;  run `pio run -e stackchan` first so the image is current.
set -eu
cd "$(dirname "$0")/.."
SCRATCH=/private/tmp/claude-501/-Users-karthik-veenam/34c4f79a-8db8-4faa-ba52-307d594d82d6/scratchpad
ESPTOOL="${ESPTOOL:-$SCRATCH/venv/bin/esptool}"; [ -x "$ESPTOOL" ] || ESPTOOL=esptool
BOOT_APP0="${BOOT_APP0:-$SCRATCH/pio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin}"
exec "$ESPTOOL" --chip esp32s3 --port "${1:-/dev/cu.usbmodem101}" write-flash -z --flash-mode keep --flash-freq keep --flash-size keep 0xe000 "$BOOT_APP0" 0x10000 .pio/build/stackchan/firmware.bin

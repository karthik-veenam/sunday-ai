#!/bin/bash
# Guard: the Classic face must stay byte-identical to the version the user approved and that was first flashed.
# Renders all 16 Classic frames (10 expressions + 6 states) with the real renderer sources and compares their SHA-256 to the baseline.
#   ./tools/preview/check_classic.sh        -> prints "Classic frames differing from baseline: 0" when untouched
set -eu
cd "$(dirname "$0")/../.."
OUT=$(mktemp -d)
g++ -std=c++17 -O2 -o "$OUT/preview" tools/preview/preview.cpp
"$OUT/preview" "$OUT" >/dev/null 2>&1
cd "$OUT"
BAD=$(shasum -a 256 -c "$OLDPWD/tools/preview/classic_baseline.sha256" 2>&1 | grep -vc ': OK' || true)
echo "Classic frames differing from baseline: $BAD"
[ "$BAD" = "0" ]

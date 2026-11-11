#!/bin/bash
# One-time installation of the Stack-chan audio backend. DRY RUN unless you pass --apply.
# It never restarts sunday.service, never enables the gateway, and leaves the backend on 'airhug', so applying it changes
# NOTHING about how Sunday runs today. It only makes `sunday-audio stackchan` possible.
set -eu
H=/home/djpi/sunday-audio
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
run() { echo "  + $*"; if [ $APPLY = 1 ]; then "$@"; fi; }
echo "install ($([ $APPLY = 1 ] && echo APPLY || echo 'dry run - pass --apply to execute'))"
echo "1. gateway key"; [ -f $H/secrets/device.key ] && echo "  key exists" || run $H/sunday-audio init-key
echo "2. gateway systemd unit (installed but NOT enabled or started)"
run sudo install -m 644 $H/systemd/sunday-stackchan-gateway.service /etc/systemd/system/sunday-stackchan-gateway.service
run sudo systemctl daemon-reload
echo "3. command on PATH"
run sudo ln -sf $H/sunday-audio /usr/local/bin/sunday-audio
echo "4. backend stays: $(cat $H/state/backend 2>/dev/null || echo 'airhug (default)')"
echo "(the sunday.service drop-in is NOT installed here: 'sunday-audio stackchan' installs it, 'sunday-audio airhug' removes it)"

#!/bin/bash
# EMERGENCY ROLLBACK: restore the original AIRHUG setup using only plain systemd commands (does not need the
# sunday-audio program, python, or the gateway). Safe to run at any time, as often as you like.
set -u
H=/home/djpi/sunday-audio
echo "[1/5] stopping production and the Stack-chan gateway"
sudo systemctl stop sunday.service
sudo systemctl stop sunday-stackchan-gateway.service 2>/dev/null
sudo systemctl disable sunday-stackchan-gateway.service 2>/dev/null
echo "[2/5] removing the PATH-shim drop-in (production returns to its original configuration)"
sudo rm -f /etc/systemd/system/sunday.service.d/audio-backend.conf
sudo systemctl daemon-reload
echo "[3/5] recording backend = airhug"
mkdir -p $H/state && echo airhug > $H/state/backend
if grep -q '"manage_lva": *true' $H/config.json 2>/dev/null; then
  echo "[4/5] restarting the HA voice satellite container (it was managed by sunday-audio)"
  sudo docker start linux-voice-assistant >/dev/null 2>&1
else
  echo "[4/5] HA voice satellite: not managed by sunday-audio, left as is"
fi
echo "[5/5] starting production"
sudo systemctl start sunday.service
sleep 8
echo "sunday.service: $(systemctl is-active sunday.service)"
echo "AIRHUG open by production (expect an arecord line): $(pgrep -a arecord | head -1)"

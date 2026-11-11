#!/bin/bash
# Installs the Sunday prompt editor as a service on port 8095.   Run on the Pi:  bash ~/sunday-prompt-editor/install.sh
set -eu
D="$HOME/sunday-prompt-editor"; cd "$D"
echo "== 1. tests (must pass before anything is installed)"
~/sunday2-env/bin/python -m unittest test_prompt_editor 2>&1 | tail -3 | tee /tmp/spe-test.txt
grep -q "^OK" /tmp/spe-test.txt || { echo "STOPPED: tests failed"; exit 1; }
echo "== 2. service"
sudo cp "$D/sunday-prompt-editor.service" /etc/systemd/system/sunday-prompt-editor.service
sudo systemctl daemon-reload
sudo systemctl enable --now sunday-prompt-editor.service
for i in $(seq 1 20); do curl -s -m 2 http://127.0.0.1:8095/healthz >/dev/null && break; sleep 1; done
curl -s -m 3 http://127.0.0.1:8095/healthz >/dev/null && echo "   running on port 8095" || { echo "STOPPED: the service did not start (journalctl -u sunday-prompt-editor)"; exit 1; }
echo "== 3. button on the Sunday Body dashboard"
~/sunday2-env/bin/python "$D/add_dashboard_link.py"
echo
echo "Open:   http://192.168.88.108:8095/"
echo "No login needed (home network only). To require a token later: set SPE_AUTH=token in the service."

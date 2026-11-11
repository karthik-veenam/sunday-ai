"""Restore the pre-build HA satellite pipeline; does not touch old Sunday."""
import json
from pathlib import Path
import urllib.request

root = Path(__file__).parent
config = json.loads((root / "config.json").read_text())
before = json.loads((root / "deployment/before.json").read_text())
payload = {"entity_id": "select.sunday_assistant", "option": before["select.sunday_assistant"]}
request = urllib.request.Request(config["ha_url"].rstrip("/") + "/api/services/select/select_option",
    data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + config["ha_token"],
                                              "Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=15) as response:
    assert response.status == 200
print("Restored HA satellite pipeline to", payload["option"], "; old Sunday files were not touched.")

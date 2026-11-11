# Sunday prompt editor

A small web page to edit and save Sunday's two prompt files (`~/sunday2-rebuild/docs/SYSTEM_PROMPT.md` and `VOICE_PROMPT.md`).

* **Open** `http://192.168.88.108:8095/` (or the "Sunday prompt" button on the Sunday Body dashboard). **No login:** it only answers devices on the home network (private addresses), only under this machine's own address/names, and refuses cross-site requests. To require a token instead, set `SPE_AUTH=token` in the service (the token file is then created in `~/sunday-prompt-editor/token`).
* **Save** (or Ctrl/Cmd+S) writes the file and keeps the previous version in `~/sunday-prompt-editor/history/`. **Save & apply** also restarts the brain — only when Sunday is not in a conversation. The brain reads the prompt only when it starts, so a plain Save does not change her behaviour until she restarts (the page shows "saved, not applied").
* **Earlier versions**: Diff against any version, Restore any version (the current text is backed up first).
* **Safety**: only these two files can be edited; a file that changed on disk after you opened it (another agent/editor) is never overwritten; writes are atomic; token mode (optional) is rate-limited and the token file is mode 600.
* **Install**: `bash ~/sunday-prompt-editor/install.sh` (runs the tests first). Uninstall: `sudo systemctl disable --now sunday-prompt-editor && sudo rm /etc/systemd/system/sunday-prompt-editor.service`.
* Tests: `cd ~/sunday-prompt-editor && ~/sunday2-env/bin/python -m unittest test_prompt_editor` (32 tests, sandboxed).

#!/bin/bash
# Run `sunday-audio <args>` fully detached, so a dropped ssh session cannot interrupt the transaction half way.
LOG=$HOME/sunday-audio/logs/switch-run.log
: > "$LOG"
setsid nohup /home/djpi/sunday-audio/sunday-audio "$@" >> "$LOG" 2>&1 < /dev/null &
echo "started pid $!"

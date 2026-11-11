# PlatformIO pre-build hook: keep expressions_data.h in sync with expressions.json.
Import("env")  # noqa: F821
import os, subprocess, sys
subprocess.check_call([sys.executable, os.path.join(env["PROJECT_DIR"], "tools", "gen_expressions.py")])  # noqa: F821

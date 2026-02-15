#!/usr/bin/env python3
"""PostToolUse hook: Run black on edited Python files."""

import json
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

if file_path.endswith(".py"):
    subprocess.run(["black", "--quiet", file_path])

sys.exit(0)

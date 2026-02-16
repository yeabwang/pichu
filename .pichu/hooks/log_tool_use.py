#!/usr/bin/env python3
"""Example PostToolUse hook: Log all tool executions.

Appends a line to a log file for every tool that completes successfully.

Usage in config.toml:
    [[hooks.PostToolUse]]
    [[hooks.PostToolUse.hooks]]
    type = "command"
    command = "python .pichu/hooks/log_tool_use.py"
"""

import json
import os
import sys
from datetime import datetime


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})
    session_id = input_data.get("session_id", "unknown")
    cwd = input_data.get("cwd", "")

    # Build log entry
    timestamp = datetime.now().isoformat()

    # For shell commands, log the command
    if tool_name == "shell":
        detail = tool_input.get("command", "")
    # For file tools, log the path
    elif "path" in tool_input:
        detail = tool_input["path"]
    elif "file_path" in tool_input:
        detail = tool_input["file_path"]
    else:
        detail = json.dumps(tool_input)[:100]

    log_line = f"[{timestamp}] session={session_id[:8]} tool={tool_name} detail={detail}\n"

    # Append to log file in logs/hooks/ directory
    log_dir = os.path.join(cwd, "logs", "hooks")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "tool_use.log")

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_line)

    sys.exit(0)


if __name__ == "__main__":
    main()

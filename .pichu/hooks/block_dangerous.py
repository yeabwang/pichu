#!/usr/bin/env python3
"""Example PreToolUse hook: Block dangerous shell commands.

Reads JSON input from stdin, checks if the shell command matches
dangerous patterns, and returns a deny decision if so.

Usage in config.toml:
    [[hooks.PreToolUse]]
    matcher = "shell"
    [[hooks.PreToolUse.hooks]]
    type = "command"
    command = "python .pichu/hooks/block_dangerous.py"
    timeout = 10
"""

import json
import re
import sys

DANGEROUS_PATTERNS = [
    r"rm\s+(-rf?|--recursive)\s+[/~]",
    r"rm\s+-rf?\s+\*",
    r"dd\s+if=",
    r"mkfs",
    r"shutdown",
    r"reboot",
    r"chmod\s+(-R\s+)?777\s+[/~]",
    r"curl\s+.*\|\s*(bash|sh)",
    r"wget\s+.*\|\s*(bash|sh)",
]


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)  # Can't parse input, allow

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    command = tool_input.get("command", "")

    if tool_name != "shell" or not command:
        sys.exit(0)  # Not a shell command, allow

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            # Output deny decision as JSON
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Blocked dangerous command matching pattern: {pattern}",
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)

    # Command is safe, allow
    sys.exit(0)


if __name__ == "__main__":
    main()

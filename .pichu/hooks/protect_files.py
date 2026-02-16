#!/usr/bin/env python3
"""Example PreToolUse hook: Protect sensitive files from edits.

Blocks write_file and edit_file operations on files matching
sensitive patterns (.env, .git/, secrets, keys, etc.).

Usage in config.toml:
    [[hooks.PreToolUse]]
    matcher = "write_file|edit_file"
    [[hooks.PreToolUse.hooks]]
    type = "command"
    command = "python .pichu/hooks/protect_files.py"
    timeout = 5
"""

import json
import sys

PROTECTED_PATTERNS = [
    ".env",
    ".env.",  # .env.local, .env.production, etc.
    ".git/",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".key",
    "secrets",
]


def main():
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("path", "") or tool_input.get("file_path", "")

    if not file_path:
        sys.exit(0)

    # Normalize path separators
    normalized = file_path.replace("\\", "/").lower()

    for pattern in PROTECTED_PATTERNS:
        if pattern.lower() in normalized:
            # Block the edit
            print(
                f"Protected file: {file_path} matches pattern '{pattern}'",
                file=sys.stderr,
            )
            sys.exit(2)  # Exit code 2 = blocking error

    sys.exit(0)


if __name__ == "__main__":
    main()

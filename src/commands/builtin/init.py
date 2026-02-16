"""Initialize project with AGENTS.md."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


_AGENTS_TEMPLATE = """# {project_name}

## Architecture
- Main runtime modules and boundaries:
-

## Tech Stack
- Languages, frameworks, and critical infrastructure:
-

## Patterns
- Reusable implementation patterns contributors should follow:
-

## Conventions
- Naming, formatting, and review expectations:
-

## Gotchas
- Known pitfalls, environment constraints, or unsafe operations:
-

## Testing
- Commands to run and quality gates before merge:
-
"""

_PROJECT_CONFIG_TEMPLATE = """# Pichu project configuration
# Tip: use /login for llm.base_url and llm.model so user-level settings stay centralized.

debug = false

[llm]
temperature = 0
timeout = 120.0

[llm.retry]
max_attempts = 5
min_wait = 2.0
max_wait = 60.0
multiplier = 1.0

[limits]
max_turns = 100
max_tool_output_tokens = 50000
max_file_size = 10485760
context_window = 128000
tool_output_display_tokens = 2000
tool_output_preview_tokens = 200
prune_protect_tokens = 10000
prune_minimum_tokens = 5000
compression_threshold = 0.8

[list_dir]
default_depth = 2
max_depth = 5
default_limit = 100
max_limit = 500
respect_gitignore = true
include_hidden = false
skip_dirs = [
    "node_modules",
    ".npm",
    ".yarn",
    ".pnpm-store",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "env",
    ".eggs",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".nuxt",
    ".output",
    ".git",
    ".svn",
    ".hg",
    ".idea",
]

[grep]
default_limit = 100
max_limit = 2000
default_context_lines = 0
max_context_lines = 10
max_file_size = 1000000
timeout = 30
default_excludes = [
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
]
binary_extensions = [
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".pyc", ".pyo", ".class", ".o", ".obj", ".a", ".lib",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".sqlite", ".db", ".pickle", ".pkl",
]

[glob]
default_limit = 200
max_limit = 5000
timeout = 30
skip_dirs = [
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".git",
    "dist",
    "build",
    ".next",
    "target",
    ".idea",
]

[web_search]
default_backend = "serper"
fallback_backend = "ddgs"
max_results = 5
max_result_chars = 2000
region = "us-en"
safesearch = "moderate"

[web_search.cache]
enabled = true
mode = "cached"
cache_dir = ".pichu/cache"
search_ttl_hours = 24.0
fetch_ttl_hours = 168.0
max_size_mb = 100.0

[web_search.rate_limit]
enabled = true
serper_rpm = 50
ddgs_rpm = 30
fetch_rpm = 60

[web_search.retry]
max_attempts = 3
base_delay = 1.0
max_delay = 30.0
exponential_base = 2.0
jitter = true

[web_search.security]
allowed_domains = []
blocked_domains = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
]
allow_ip_addresses = false
allow_localhost = false
allow_private_networks = false
allowed_schemes = ["http", "https"]
require_url_provenance = true

[web_search.budget]
max_searches = 20
max_results_per_search = 10
max_fetches = 30
max_bytes_total = 10485760

[web_fetch]
use_playwright = true
playwright_timeout = 10000
httpx_timeout = 30
verify_ssl = true
max_content_length = 50000
strip_scripts = true
strip_styles = true
pdf_enabled = true
pdf_max_pages = 50
user_agent = "Pichu/1.0"
find_context_lines = 2
find_max_matches = 10

[mcp_servers.fetch]
command = "npx"
args = ["-y", "fetch-mcp"]

[mcp_servers.github]
url = "https://api.githubcopilot.com/mcp/"
transport = "http"
startup_timeout_sec = 60.0

[approval]
policy = "on_request"
remember_session = true

[approval.rules]
allow = ["task_create", "task_update", "task_get", "task_list"]

[hooks]

[[hooks.PostToolUse]]
[[hooks.PostToolUse.hooks]]
type = "command"
command = "python .pichu/hooks/log_tool_use.py"

[[hooks.PostToolUse]]
matcher = "write_file|edit_file"
[[hooks.PostToolUse.hooks]]
type = "command"
command = "python .pichu/hooks/auto_format.py"

[[hooks.PreToolUse]]
matcher = "shell"
[[hooks.PreToolUse.hooks]]
type = "command"
command = "python .pichu/hooks/block_dangerous.py"
timeout = 10

[[hooks.PreToolUse]]
matcher = "write_file|edit_file"
[[hooks.PreToolUse.hooks]]
type = "command"
command = "python .pichu/hooks/protect_files.py"
timeout = 5
"""


class InitCommand(SlashCommand):
    name = "init"
    description = "Initialize project with AGENTS.md"
    usage = "/init"
    aliases: list[str] = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        import os

        project_root = config.cwd
        agents_file = project_root / "AGENTS.md"
        local_file = project_root / "AGENTS.local.md"
        project_dir_name = os.environ.get("PICHU_PROJECT_DIR", ".pichu")
        config_file_name = os.environ.get("PICHU_CONFIG_FILE", "config.toml")
        pichu_dir = project_root / project_dir_name

        created = []

        # Create AGENTS.md if not exists
        if agents_file.exists():
            tui.console.print(f"  [dim]AGENTS.md already exists at {agents_file}[/dim]")
        else:
            project_name = project_root.name
            content = _AGENTS_TEMPLATE.format(project_name=project_name)
            agents_file.write_text(content, encoding="utf-8")
            created.append("AGENTS.md")

        # Create AGENTS.local.md stub
        if not local_file.exists():
            local_file.write_text(
                "# Local Instructions (gitignored)\n\n## Personal Preferences\n\n## Local Environment\n\n",
                encoding="utf-8",
            )
            created.append("AGENTS.local.md")

        # Create project config directory
        if not pichu_dir.exists():
            pichu_dir.mkdir(parents=True, exist_ok=True)
            created.append(f"{project_dir_name}/")

        # Create project config file stub
        config_file = pichu_dir / config_file_name
        if not config_file.exists():
            config_file.write_text(_PROJECT_CONFIG_TEMPLATE, encoding="utf-8")
            created.append(f"{project_dir_name}/{config_file_name}")

        # Suggest adding AGENTS.local.md to .gitignore
        gitignore = project_root / ".gitignore"
        gitignore_updated = False
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            if "AGENTS.local.md" not in content:
                with open(gitignore, "a", encoding="utf-8") as f:
                    f.write("\n# Pichu local config\nAGENTS.local.md\n")
                gitignore_updated = True

        tui.console.print()
        if created:
            for item in created:
                tui.console.print(f"  [success]✓[/success] Created {item}")
        else:
            tui.console.print("  [dim]Project already initialized — all files exist.[/dim]")

        if gitignore_updated:
            tui.console.print("  [success]✓[/success] Added AGENTS.local.md to .gitignore")

        tui.console.print()
        tui.console.print("  [dim]Edit AGENTS.md to teach Pichu about your project.[/dim]")
        tui.console.print("  [dim]Use AGENTS.local.md for personal preferences (gitignored).[/dim]")
        tui.console.print("  [dim]Run /login to configure provider and model.[/dim]")
        tui.console.print()

        return CommandResult()

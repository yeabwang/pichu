"""Load and merge runtime configuration from file + environment sources."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from config.config import Config, set_config
from utils.exceptions import ConfigError

# Load .env file from cwd
load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_DIR_NAME = os.environ.get("PICHU_PROJECT_DIR", ".pichu")
CONFIG_FILE_NAME = os.environ.get("PICHU_CONFIG_FILE", "config.toml")
AGENT_MD_FILE_NAME = os.environ.get("PICHU_AGENT_FILE", "AGENT.md")
APP_NAME = os.environ.get("PICHU_APP_NAME", "pichu")


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """Represents one file-backed configuration source."""

    name: str
    path: Path
    required: bool = False


def _env_bool(key: str) -> bool:
    """Return whether an env var is explicitly truthy."""
    return os.environ.get(key, "").lower() in ("1", "true", "yes")


def get_config_dir() -> Path:
    """Return the canonical system config directory."""
    return Path.home() / f".{APP_NAME}"


def get_system_config_path() -> Path:
    """Get the system config file path."""
    return get_config_dir() / CONFIG_FILE_NAME


def _parse_toml_file(file_path: Path) -> dict[str, Any]:
    """Parse a TOML file into a dictionary."""
    try:
        from tomlkit import load as toml_load

        with open(file_path, "r", encoding="utf-8") as f:
            return dict(toml_load(f))
    except ImportError:
        raise ConfigError("tomlkit not installed. Run: pip install tomlkit")
    except Exception as e:
        raise ConfigError(
            f"Error parsing {file_path}: {e}",
            config_file=str(file_path),
            cause=e,
        ) from e


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries, with override taking precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _find_project_dir_upward(cwd: Path) -> Path | None:
    """Find the nearest project config directory from `cwd` upward."""
    current = cwd.resolve()

    while True:
        candidate = current / PROJECT_DIR_NAME
        if candidate.exists() and candidate.is_dir():
            return candidate

        if current.parent == current:
            break
        current = current.parent

    return None


def _find_file_upward(cwd: Path, filename: str) -> Path | None:
    """Find a file inside the nearest discovered project config directory."""
    project_dir = _find_project_dir_upward(cwd)
    if project_dir:
        candidate = project_dir / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_agent_instructions(cwd: Path) -> str | None:
    """Load developer instructions from the nearest project AGENT.md."""
    agent_md = _find_file_upward(cwd, AGENT_MD_FILE_NAME)
    if agent_md is None:
        return None

    try:
        return agent_md.read_text(encoding="utf-8")
    except (OSError, IOError) as e:
        logger.warning(f"Failed to read {AGENT_MD_FILE_NAME}: {e}")
        return None


def _collect_file_sources(cwd: Path) -> list[ConfigSource]:
    """Resolve file sources in merge order (system -> project)."""
    sources: list[ConfigSource] = []
    system_path = get_system_config_path()
    if system_path.exists() and system_path.is_file():
        sources.append(ConfigSource(name="system", path=system_path, required=False))

    project_path = _find_file_upward(cwd, CONFIG_FILE_NAME)
    if project_path:
        sources.append(ConfigSource(name="project", path=project_path, required=True))
    return sources


def _load_file_sources(sources: list[ConfigSource]) -> dict[str, Any]:
    """Load and merge file-backed config sources in order."""
    merged: dict[str, Any] = {}
    for source in sources:
        try:
            merged = _merge_dicts(merged, _parse_toml_file(source.path))
            logger.debug("Loaded %s config from %s", source.name, source.path)
        except ConfigError as error:
            if source.required:
                logger.warning("Failed to load %s config: %s", source.name, error)
                raise
            logger.warning("Failed to load %s config: %s", source.name, error)
    return merged


def load_config(cwd: Path | None = None) -> Config:
    """Load configuration from all sources.

    Merge order:
    1. Dataclass defaults
    2. System config (`~/.pichu/config.toml`)
    3. Project config (`<repo>/.pichu/config.toml`)
    4. System .env (`~/.pichu/.env` — API keys from /login)
    5. Project .env (cwd `.env` — loaded at import time)
    6. Environment overrides (`LLM_API_KEY`, `PICHU_DEBUG`)

    Args:
        cwd: Working directory to use. Defaults to current directory.

    Returns:
        Fully configured Config instance.

    Raises:
        ConfigError: If there's an error parsing config files.
    """
    cwd = (cwd or Path.cwd()).resolve()
    config_data = _load_file_sources(_collect_file_sources(cwd))

    # Load .env from system config dir (keys saved by /login).
    # override=False so project-level / cwd .env and real env vars take priority.
    system_env = get_config_dir() / ".env"
    if system_env.exists():
        load_dotenv(system_env, override=False)

    # Start from schema defaults.
    config = Config(cwd=cwd)

    # Merge file-backed settings.
    if config_data:
        config.update_from_dict(config_data)

    # Apply environment overrides.
    if api_key := os.environ.get("LLM_API_KEY"):
        config.llm.api_key = api_key
    if _env_bool("PICHU_DEBUG"):
        config.debug = True

    # Load runtime developer instructions.
    config.developer_instructions = _load_agent_instructions(cwd)

    # Publish singleton for runtime consumers.
    set_config(config)

    return config


def ensure_config_dir() -> Path:
    """Ensure the system config directory exists."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def create_default_config_file(path: Path | None = None) -> Path:
    """Create a default config file at the given path."""
    if path is None:
        path = get_system_config_path()

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    default_config = """# pichu Configuration
# See documentation for all options

[llm]
# Provider-agnostic LLM settings
# Can also be set via environment variables:
# LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT

# base_url = "https://openrouter.ai/api/v1"
# model = "mistralai/devstral-small:free"
# temperature = 0.3
# timeout = 120.0

[llm.retry]
# max_attempts = 5
# min_wait = 2.0
# max_wait = 60.0

[limits]
# max_turns = 100
# max_tool_output_tokens = 50000
# context_window = 128000

[approval]
# policy: on_request | auto | auto_edit | plan | never | yolo
# policy = "on_request"
# remember_session = true
# remember_permanent = false

# [approval.rules]
# allow = ["shell(npm run *)", "shell(git status)"]
# deny = ["shell(curl * | bash)", "read_file(.env)"]
# ask = ["shell(git push *)"]

# [safety.sandbox]
# enabled = true
# restrict_to_cwd = true
# allow_temp_writes = false
# allow_executables = false
# restrict_reads_to_allowed_dirs = false
# allowed_directories = []
# blocked_patterns = [".env", ".ssh", ".aws/credentials"]
# blocked_extensions = [".exe", ".dll", ".ps1", ".sh"]
# sensitive_read_patterns = [".env", ".ssh/id_", ".aws/credentials"]

# [logging]
# level = "INFO" # DEBUG | INFO | WARNING | ERROR
# per_logger_levels = { "httpx" = "WARNING" }

# [logging.console]
# enabled = false
# level = "DEBUG"
# use_rich = true
# show_path = false
# markup = true

# [logging.file]
# enabled = true
# level = "INFO"
# path = "logs/app/pichu.log"
# max_bytes = 5242880
# backup_count = 5
# json = false

# [logging.audit]
# enabled = true
# level = "INFO"
# path = "logs/security/audit.log"
# max_bytes = 5242880
# backup_count = 5
# json = true

# ── Loop Detection ───────────────────────────────────────────────
# Detects repetitive agent behavior and takes corrective action.

# [loop_detection]
# enabled = true
# max_repeated_tool_calls = 3    # Same tool+args N times triggers detection
# max_repeated_errors = 3         # Consecutive identical errors threshold
# max_similar_responses = 3       # Near-identical LLM responses threshold
# similarity_threshold = 0.85     # Text similarity ratio (0.0–1.0)
# window_size = 10                # Sliding look-back window
# action = "warn_and_nudge"       # "warn_and_nudge" | "pause" | "stop"

# ── Hooks ────────────────────────────────────────────────────────
# Hooks are user-defined shell commands that run at lifecycle points.
# See documentation for all hook events and their input/output schemas.

# [hooks]
# disabled = false

# [[hooks.PreToolUse]]
# matcher = "shell"
# [[hooks.PreToolUse.hooks]]
# type = "command"
# command = "python .pichu/hooks/block-dangerous.py"
# timeout = 10

# [[hooks.PostToolUse]]
# matcher = "write_file|edit_file"
# [[hooks.PostToolUse.hooks]]
# type = "command"
# command = "python .pichu/hooks/auto-format.py"

# [[hooks.Stop]]
# [[hooks.Stop.hooks]]
# type = "command"
# command = "python .pichu/hooks/verify-tests.py"
# timeout = 120

# [instructions]
# developer_instructions = "Custom instructions for the agent"
"""

    path.write_text(default_config, encoding="utf-8")
    return path

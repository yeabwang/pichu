"""Load sub-agent definitions from user and project markdown files."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from utils.subagent_types import SubAgentConfig

logger = logging.getLogger(__name__)


class SubAgentLoader:
    """
    Loader for sub-agent markdown files.

    Loads sub-agents from two locations with priority:
    1. Project-level: <project_root>/sub_agents/*.md (higher priority)
    2. User-level: ~/.pichu/sub_agents/*.md (lower priority)

    When names conflict, project-level sub-agents take precedence.

    Example:
        loader = SubAgentLoader(project_root=Path("/my/project"))
        agents = loader.load_all()

        for name, config in agents.items():
            print(f"Loaded sub-agent: {name}")
    """

    # Default user-level agents directory
    DEFAULT_USER_AGENTS_DIR = Path.home() / ".pichu" / "sub_agents"

    # Default project-level agents directory name
    DEFAULT_PROJECT_AGENTS_DIR = "sub_agents"
    _AGENT_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")

    def __init__(
        self,
        project_root: Path | str | None = None,
        user_agents_dir: Path | str | None = None,
        project_agents_dir: str = DEFAULT_PROJECT_AGENTS_DIR,
    ):
        """
        Initialize the sub-agent loader.

        Args:
            project_root: Path to the project root directory.
            user_agents_dir: Path to user-level agents directory.
                            Defaults to ~/.pichu/sub_agents/
            project_agents_dir: Name of the project-level agents directory.
                               Defaults to "sub_agents".
        """
        self._project_root = Path(project_root) if project_root else None
        self._user_agents_dir = Path(user_agents_dir) if user_agents_dir else self.DEFAULT_USER_AGENTS_DIR
        self._project_agents_dir = project_agents_dir
        self._cache: dict[str, SubAgentConfig] | None = None

    @property
    def project_agents_path(self) -> Path | None:
        """Get the project-level agents directory path."""
        if self._project_root:
            return self._project_root / self._project_agents_dir
        return None

    @property
    def user_agents_path(self) -> Path:
        """Get the user-level agents directory path."""
        return self._user_agents_dir

    def load_all(self, force_reload: bool = False) -> dict[str, SubAgentConfig]:
        """
        Load all sub-agents from both user and project directories.

        Project-level agents take precedence over user-level agents
        with the same name.

        Args:
            force_reload: If True, reload from disk even if cached.

        Returns:
            Dictionary mapping agent names to their configurations.
        """
        if self._cache is not None and not force_reload:
            return self._cache

        agents: dict[str, SubAgentConfig] = {}

        # Load user-level agents (lower priority)
        user_agents = self._load_from_directory(self._user_agents_dir)
        for name, config in user_agents.items():
            agents[name] = config
            logger.debug(f"Loaded user-level sub-agent: {name}")

        # Load project-level agents (higher priority, overrides user-level)
        if self._project_root:
            project_dir = self._project_root / self._project_agents_dir
            project_agents = self._load_from_directory(project_dir)
            for name, config in project_agents.items():
                if name in agents:
                    logger.debug(f"Project sub-agent '{name}' overrides user-level agent")
                agents[name] = config
                logger.debug(f"Loaded project-level sub-agent: {name}")

        self._cache = agents
        logger.info(f"Loaded {len(agents)} sub-agent(s)")
        return agents

    def get_all(self) -> list[SubAgentConfig]:
        """
        Get all loaded sub-agents as a list.

        Returns:
            List of all loaded SubAgentConfig objects.
            Returns empty list if load_all() hasn't been called yet.
        """
        if self._cache is None:
            return []
        return list(self._cache.values())

    def get_by_name(self, name: str) -> SubAgentConfig | None:
        """
        Get a specific sub-agent by name.

        Args:
            name: The name of the sub-agent to retrieve.

        Returns:
            SubAgentConfig if found, None otherwise.
        """
        if self._cache is None:
            return None
        return self._cache.get(name)

    def _load_from_directory(self, directory: Path) -> dict[str, SubAgentConfig]:
        """
        Load all sub-agent files from a directory.

        Args:
            directory: Path to the directory containing .md files.

        Returns:
            Dictionary mapping agent names to their configurations.
        """
        agents: dict[str, SubAgentConfig] = {}

        if not directory.exists():
            logger.debug(f"Sub-agent directory does not exist: {directory}")
            return agents

        for file_path in directory.glob("*.md"):
            try:
                config = self.parse_file(file_path)
                if config:
                    agents[config.name] = config
            except Exception as e:
                logger.warning(f"Failed to load sub-agent from {file_path}: {e}")

        return agents

    def parse_file(self, path: Path) -> SubAgentConfig | None:
        """
        Parse a sub-agent markdown file.

        The file format is:
            ---
            name: agent-name
            description: When this agent should be invoked
            tools: ["Read", "Write", "Edit"]
            model: sonnet
            ---

            System prompt content goes here...

        Args:
            path: Path to the markdown file.

        Returns:
            SubAgentConfig if valid, None if invalid or empty.
        """
        if not path.exists():
            logger.warning(f"Sub-agent file does not exist: {path}")
            return None

        content = path.read_text(encoding="utf-8")
        return self.parse_content(content, path)

    def parse_content(self, content: str, source_path: Path | None = None) -> SubAgentConfig | None:
        """
        Parse sub-agent content from a string.

        Args:
            content: The raw markdown content with YAML frontmatter.
            source_path: Optional path to the source file.

        Returns:
            SubAgentConfig if valid, None if invalid.
        """
        # Check for YAML frontmatter
        if not content.strip().startswith("---"):
            logger.warning(f"Sub-agent file missing YAML frontmatter: {source_path}")
            return None

        # Split frontmatter and body
        parts = content.split("---", 2)
        if len(parts) < 3:
            logger.warning(f"Invalid frontmatter format in sub-agent file: {source_path}")
            return None

        frontmatter_str = parts[1].strip()
        system_prompt = parts[2].strip()

        # Parse YAML frontmatter
        try:
            frontmatter = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse YAML frontmatter: {e}")
            return None

        if not frontmatter:
            logger.warning(f"Empty frontmatter in sub-agent file: {source_path}")
            return None

        # Validate required fields
        if "name" not in frontmatter:
            logger.warning(f"Missing 'name' in sub-agent frontmatter: {source_path}")
            return None

        if "description" not in frontmatter:
            logger.warning(f"Missing 'description' in sub-agent frontmatter: {source_path}")
            return None

        raw_name = str(frontmatter["name"]).strip()
        if not self._AGENT_NAME_PATTERN.match(raw_name):
            logger.warning(
                f"Invalid sub-agent name '{raw_name}' in {source_path}; "
                "expected lowercase letters, numbers, and hyphens"
            )
            return None

        # Parse tools field (can be string, list, or None)
        tools = self._parse_tools_field(frontmatter.get("tools"))
        disallowed_tools = self._parse_tools_field(
            frontmatter.get("disallowedTools") or frontmatter.get("disallowed_tools")
        )

        # Parse skills field
        skills = frontmatter.get("skills")
        if isinstance(skills, str):
            parsed_skills = (skill.strip() for skill in skills.split(","))
            skills = [skill for skill in parsed_skills if skill]

        max_turns = frontmatter.get("maxTurns", frontmatter.get("max_turns"))
        if max_turns is not None:
            try:
                max_turns = int(max_turns)
            except (TypeError, ValueError):
                logger.warning(f"Invalid maxTurns value for sub-agent '{raw_name}' in {source_path}")
                max_turns = None

        return SubAgentConfig(
            name=raw_name,
            description=frontmatter["description"],
            system_prompt=system_prompt,
            tools=tools,
            disallowed_tools=disallowed_tools,
            model=frontmatter.get("model"),
            permission_mode=frontmatter.get("permissionMode", "default"),
            max_turns=max_turns,
            skills=skills,
            hooks=frontmatter.get("hooks"),
            color=frontmatter.get("color"),
            source_path=source_path,
        )

    def _parse_tools_field(self, tools: Any) -> list[str] | None:
        """
        Parse the tools field which can be various formats.

        Supports:
            - None (inherit all)
            - List: ["Read", "Write", "Edit"]
            - Comma-separated string: "Read, Write, Edit"
            - JSON-like string: '["Read", "Write"]'

        Args:
            tools: The raw tools field value.

        Returns:
            List of tool names, or None to inherit all.
        """
        if tools is None:
            return None

        if isinstance(tools, list):
            return self._dedupe_non_empty_strings([str(t) for t in tools])

        if isinstance(tools, str):
            tools = tools.strip()

            # Handle empty string
            if not tools:
                return None

            # Handle JSON-like format
            if tools.startswith("["):
                try:
                    import json

                    parsed = json.loads(tools)
                    if isinstance(parsed, list):
                        return self._dedupe_non_empty_strings([str(t) for t in parsed])
                except json.JSONDecodeError:
                    pass

            # Handle comma-separated format
            return self._dedupe_non_empty_strings(tools.split(","))

        return None

    def _dedupe_non_empty_strings(self, values: list[str]) -> list[str]:
        """Normalize, filter, and deduplicate string values while preserving order."""
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped

    def reload(self) -> dict[str, SubAgentConfig]:
        """Force reload all sub-agents from disk."""
        self._cache = None
        return self.load_all(force_reload=True)

    def get(self, name: str) -> SubAgentConfig | None:
        """
        Get a specific sub-agent by name.

        Args:
            name: The sub-agent name.

        Returns:
            SubAgentConfig if found, None otherwise.
        """
        agents = self.load_all()
        return agents.get(name)

    def list_names(self) -> list[str]:
        """Get list of all loaded sub-agent names."""
        return list(self.load_all().keys())

    def create_template(
        self,
        name: str,
        description: str = "Describe when this agent should be invoked",
        tools: list[str] | None = None,
        model: str | None = None,
        directory: Path | None = None,
    ) -> Path:
        """
        Create a template sub-agent file.

        Args:
            name: The sub-agent name (lowercase, hyphens).
            description: Description of when to invoke.
            tools: List of allowed tools (None = inherit all).
            model: Model to use (None = inherit from config).
            directory: Where to create (defaults to project agents dir).

        Returns:
            Path to the created file.
        """
        if directory is None:
            if self._project_root:
                directory = self._project_root / self._project_agents_dir
            else:
                directory = self._user_agents_dir

        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"{name}.md"

        # Build frontmatter
        frontmatter_lines = [
            f"name: {name}",
            f"description: {description}",
        ]

        if tools:
            tools_str = ", ".join(tools)
            frontmatter_lines.append(f"tools: [{tools_str}]")

        if model:
            frontmatter_lines.append(f"model: {model}")

        frontmatter = "\n".join(frontmatter_lines)

        template = f"""---
{frontmatter}
---

You are a specialized assistant for [describe role here].

## Your Role

- [Describe primary responsibilities]
- [Describe expertise areas]
- [Describe approach to problems]

## When Invoked

1. [First step to take]
2. [Second step to take]
3. [Third step to take]

## Guidelines

- [Guideline 1]
- [Guideline 2]
- [Guideline 3]

## Output Format

Provide your response in a clear, structured format:
- [Format details]
"""

        file_path.write_text(template, encoding="utf-8")
        logger.info(f"Created sub-agent template: {file_path}")

        # Invalidate cache
        self._cache = None

        return file_path

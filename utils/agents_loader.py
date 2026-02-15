"""
AGENTS.md Loader - Hierarchical loading of agent instruction files.

Implements a three-tier hierarchy:
1. Global: ~/.pichu/AGENTS.md (user preferences, cross-project patterns)
2. Project: <project>/AGENTS.md (project-level instructions)
3. Local: <project>/AGENTS.local.md (personal, gitignored)

Also supports directory-level AGENTS.md files that apply to
code within that directory (with local scoping).

File Format:
    # Project Name

    ## Architecture
    - System uses FastAPI for backend
    - PostgreSQL with SQLAlchemy ORM

    ## Patterns
    - Use dataclasses for DTOs
    - Always use type hints

    ## Gotchas
    - Don't import X from Y (circular import)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AgentsSection:
    """A section from an AGENTS.md file."""

    name: str
    content: list[str]
    raw: str = ""

    @property
    def as_text(self) -> str:
        """Get content as plain text."""
        return "\n".join(self.content)

    def __str__(self) -> str:
        return f"## {self.name}\n" + "\n".join(f"- {item}" for item in self.content)


@dataclass
class AgentsFile:
    """Parsed AGENTS.md file."""

    path: Path
    title: str = ""
    sections: dict[str, AgentsSection] = field(default_factory=dict)
    raw_content: str = ""
    loaded_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def parse(cls, path: Path) -> "AgentsFile":
        """Parse an AGENTS.md file."""
        if not path.exists():
            return cls(path=path)

        content = path.read_text(encoding="utf-8")
        return cls.from_string(content, path)

    @classmethod
    def from_string(cls, content: str, path: Path | None = None) -> "AgentsFile":
        """Parse AGENTS content from string."""
        result = cls(
            path=path or Path("AGENTS.md"),
            raw_content=content,
        )

        lines = content.split("\n")
        current_section: AgentsSection | None = None

        for line in lines:
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith("<!--"):
                continue

            # Title (# Header)
            if stripped.startswith("# ") and not result.title:
                result.title = stripped[2:].strip()
                continue

            # Section header (## Header)
            if stripped.startswith("## "):
                section_name = stripped[3:].strip().lower()
                # Normalize section names
                section_name = cls._normalize_section_name(section_name)
                current_section = AgentsSection(name=section_name, content=[], raw=line)
                result.sections[section_name] = current_section
                continue

            # List item under section
            if current_section and stripped.startswith("- "):
                item = stripped[2:].strip()
                if item:
                    current_section.content.append(item)

        return result

    @staticmethod
    def _normalize_section_name(name: str) -> str:
        """Normalize section name to standard form."""
        name = name.lower().strip()

        # Map variations to canonical names
        mappings = {
            "arch": "architecture",
            "design": "architecture",
            "structure": "architecture",
            "decisions": "decision",
            "choices": "decision",
            "patterns": "pattern",
            "conventions": "pattern",
            "style": "pattern",
            "gotchas": "gotcha",
            "pitfalls": "gotcha",
            "warnings": "gotcha",
            "watch out": "gotcha",
            "preferences": "user_preference",
            "prefs": "user_preference",
            "user prefs": "user_preference",
        }

        return mappings.get(name, name)

    @property
    def is_empty(self) -> bool:
        """Check if file has no content."""
        return not self.sections

    @property
    def section_names(self) -> list[str]:
        """Get list of section names."""
        return list(self.sections.keys())

    def get_section(self, name: str) -> AgentsSection | None:
        """Get a section by name."""
        normalized = self._normalize_section_name(name)
        return self.sections.get(normalized)

    def get_all_items(self) -> list[tuple[str, str]]:
        """Get all items as (section, content) tuples."""
        items = []
        for section_name, section in self.sections.items():
            for item in section.content:
                items.append((section_name, item))
        return items

    def to_markdown(self) -> str:
        """Convert back to markdown format."""
        lines = []

        if self.title:
            lines.append(f"# {self.title}\n")

        for section in self.sections.values():
            lines.append(str(section))
            lines.append("")

        return "\n".join(lines)


@dataclass
class AgentsContext:
    """
    Combined context from all AGENTS files in hierarchy.

    Merges global, project, and local files with proper precedence.
    """

    global_file: AgentsFile | None = None
    project_file: AgentsFile | None = None
    local_file: AgentsFile | None = None
    directory_files: dict[str, AgentsFile] = field(default_factory=dict)

    @property
    def all_files(self) -> list[AgentsFile]:
        """Get all loaded files in precedence order (lowest to highest)."""
        files = []
        if self.global_file and not self.global_file.is_empty:
            files.append(self.global_file)
        if self.project_file and not self.project_file.is_empty:
            files.append(self.project_file)
        if self.local_file and not self.local_file.is_empty:
            files.append(self.local_file)
        files.extend(self.directory_files.values())
        return files

    def get_merged_section(self, name: str) -> list[str]:
        """
        Get all items from a section across all files.

        Higher precedence files come later (can override).
        """
        items = []

        for agents_file in self.all_files:
            section = agents_file.get_section(name)
            if section:
                items.extend(section.content)

        return items

    def get_all_sections(self) -> dict[str, list[str]]:
        """Get all sections merged across all files."""
        all_sections: dict[str, list[str]] = {}

        for agents_file in self.all_files:
            for section_name, section in agents_file.sections.items():
                if section_name not in all_sections:
                    all_sections[section_name] = []
                all_sections[section_name].extend(section.content)

        return all_sections

    def to_prompt_context(self) -> str:
        """
        Convert to context string for LLM prompt injection.

        Returns formatted markdown suitable for system prompt.
        """
        lines = ["## 📋 Project Instructions (from AGENTS.md)\n"]

        all_sections = self.get_all_sections()

        if not all_sections:
            return ""

        # Section display order and emojis
        section_order = [
            ("architecture", "🏗️"),
            ("decision", "🎯"),
            ("pattern", "📐"),
            ("gotcha", "⚠️"),
            ("context", "📝"),
            ("user_preference", "👤"),
        ]

        displayed = set()

        # Display in preferred order
        for section_name, emoji in section_order:
            if section_name in all_sections:
                items = all_sections[section_name]
                lines.append(f"\n### {emoji} {section_name.title()}\n")
                for item in items:
                    lines.append(f"- {item}")
                displayed.add(section_name)

        # Display any remaining sections
        for section_name, items in all_sections.items():
            if section_name not in displayed:
                lines.append(f"\n### {section_name.title()}\n")
                for item in items:
                    lines.append(f"- {item}")

        lines.append("\n---\n")
        return "\n".join(lines)

    def get_relevant_context(self, file_path: str | Path | None = None) -> str:
        """
        Get context relevant to a specific file.

        Includes directory-level AGENTS.md if applicable.
        """
        context = self.to_prompt_context()

        if file_path:
            file_path = Path(file_path)

            # Check for directory-specific context
            for dir_path, agents_file in self.directory_files.items():
                if str(file_path).startswith(dir_path):
                    context += f"\n### 📁 Directory-specific ({dir_path})\n"
                    for section in agents_file.sections.values():
                        for item in section.content:
                            context += f"- {item}\n"

        return context


class AgentsLoader:
    """
    Loader for AGENTS.md files in the hierarchy.

    Implements the three-tier hierarchy:
    1. Global: Configurable, default ~/.pichu/AGENTS.md
    2. Project: Configurable, default <project>/AGENTS.md
    3. Local: Configurable, default <project>/AGENTS.local.md (gitignored)
    """

    def __init__(
        self,
        project_root: Path | str | None = None,
        global_agents_path: Path | None = None,
        project_agents_file: str = "AGENTS.md",
        local_agents_file: str = "AGENTS.local.md",
    ):
        """
        Initialize loader.

        Args:
            project_root: Path to project root directory
            global_agents_path: Path to global AGENTS.md (from config)
            project_agents_file: Name of project AGENTS.md file
            local_agents_file: Name of local (gitignored) AGENTS.md file
        """
        self._project_root = Path(project_root) if project_root else None
        self._global_path = global_agents_path or (Path.home() / ".pichu" / "AGENTS.md")
        self._project_agents_file = project_agents_file
        self._local_agents_file = local_agents_file
        self._context: AgentsContext | None = None

    @property
    def project_root(self) -> Path | None:
        """Get project root path."""
        return self._project_root

    @property
    def global_path(self) -> Path:
        """Get global AGENTS.md path."""
        return self._global_path

    def load(self, include_directories: bool = True) -> AgentsContext:
        """
        Load all AGENTS files and build context.

        Args:
            include_directories: Scan for directory-level AGENTS.md

        Returns:
            Merged AgentsContext from all files
        """
        context = AgentsContext()

        # Load global file
        if self._global_path.exists():
            context.global_file = AgentsFile.parse(self._global_path)
            logger.debug(f"Loaded global AGENTS.md: {len(context.global_file.sections)} sections")

        # Load project files
        if self._project_root:
            project_path = self._project_root / self._project_agents_file
            local_path = self._project_root / self._local_agents_file

            if project_path.exists():
                context.project_file = AgentsFile.parse(project_path)
                logger.debug(f"Loaded project AGENTS.md: {len(context.project_file.sections)} sections")

            if local_path.exists():
                context.local_file = AgentsFile.parse(local_path)
                logger.debug(f"Loaded local AGENTS.local.md: {len(context.local_file.sections)} sections")

            # Load directory-level files
            if include_directories:
                context.directory_files = self._load_directory_files()

        self._context = context
        return context

    def _load_directory_files(self) -> dict[str, AgentsFile]:
        """Load AGENTS.md files from subdirectories."""
        if not self._project_root:
            return {}

        directory_files: dict[str, AgentsFile] = {}

        # Walk project tree looking for AGENTS.md files (use configured name)
        for agents_path in self._project_root.rglob(self._project_agents_file):
            # Skip root level (already loaded)
            if agents_path.parent == self._project_root:
                continue

            # Skip hidden directories and common non-code directories
            rel_path = agents_path.relative_to(self._project_root)
            parts = rel_path.parts

            skip_dirs = {
                ".git",
                ".venv",
                "venv",
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                "dist",
                "build",
                ".tox",
            }

            if any(part in skip_dirs for part in parts):
                continue

            # Load the file
            agents_file = AgentsFile.parse(agents_path)
            if not agents_file.is_empty:
                dir_path = str(agents_path.parent)
                directory_files[dir_path] = agents_file
                logger.debug(f"Loaded directory AGENTS.md: {dir_path}")

        return directory_files

    def get_context(self) -> AgentsContext:
        """Get loaded context (loads if not already loaded)."""
        if self._context is None:
            return self.load()
        return self._context

    def reload(self) -> AgentsContext:
        """Force reload all AGENTS files."""
        self._context = None
        return self.load()

    def create_template(
        self,
        path: Path | None = None,
        project_name: str = "Project",
    ) -> Path:
        """
        Create a template AGENTS.md file.

        Args:
            path: Where to create the file (defaults to project root)
            project_name: Name for the project header

        Returns:
            Path to created file
        """
        if path is None:
            if self._project_root:
                path = self._project_root / "AGENTS.md"
            else:
                path = Path.cwd() / "AGENTS.md"

        template = f"""# {project_name}

## Architecture
- [Add key architectural decisions here]
- [Example: Backend uses FastAPI with SQLAlchemy ORM]

## Patterns
- [Add code patterns and conventions here]
- [Example: Use dataclasses for DTOs]
- [Example: Always use type hints]

## Gotchas
- [Add things to watch out for here]
- [Example: Don't import X from Y - causes circular import]

## Context
- [Add general project context here]
- [Example: This is a CLI tool for data processing]
"""

        path.write_text(template, encoding="utf-8")
        logger.info(f"Created AGENTS.md template at {path}")

        return path

    @staticmethod
    def get_gitignore_entry() -> str:
        """Get gitignore entry for local AGENTS file."""
        return "AGENTS.local.md"

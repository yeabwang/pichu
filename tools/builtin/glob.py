"""glob/file search tool.

Features:
- Full glob syntax support (*, **, ?, [], {})
- .gitignore respect
- Hidden file control
- Result limiting
- Exclude patterns
- Case-insensitive matching
- Relative/absolute path output
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from pydantic import BaseModel, Field

from safety.sandbox import get_sandbox
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from utils.paths import resolve_path

if TYPE_CHECKING:
    from config import Config


class GlobParams(BaseModel):
    """Parameters for glob tool."""

    pattern: str = Field(
        ...,
        description="Glob pattern to match (e.g., '**/*.py', 'src/**/*.ts', '*.{json,yaml}')",
    )
    path: str = Field(
        default=".",
        description="Directory to search in",
    )
    limit: int = Field(
        default=200,
        ge=1,
        le=5000,
        description="Maximum number of results to return",
    )
    include_hidden: bool = Field(
        default=False,
        description="Include hidden files and directories",
    )
    respect_gitignore: bool = Field(
        default=True,
        description="Honor .gitignore patterns",
    )
    files_only: bool = Field(
        default=True,
        description="Return only files (not directories)",
    )
    dirs_only: bool = Field(
        default=False,
        description="Return only directories (not files)",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Patterns to exclude from results",
    )
    case_sensitive: bool = Field(
        default=False,
        description="Case-sensitive pattern matching",
    )
    absolute_paths: bool = Field(
        default=False,
        description="Return absolute paths instead of relative",
    )


@dataclass
class GlobMatch:
    """Represents a matched file or directory."""

    path: Path
    relative_path: str
    is_dir: bool


class GlobTool(Tool):
    """Search for files matching glob patterns.

    Supports full glob syntax including **, ?, [], and {} patterns.
    """

    name = "glob"
    description = (
        "Search for files matching glob patterns in the workspace. "
        "Supports *, **, ?, [], {} patterns. Examples: '**/*.py' finds all Python files, "
        "'src/**/*.{ts,tsx}' finds TypeScript files in src, '**/test_*.py' finds test files."
    )
    kind = ToolKind.READ
    schema = GlobParams

    def __init__(self, config: "Config | None" = None) -> None:
        super().__init__(config)
        self._glob_config = config.glob if config and hasattr(config, "glob") else None

    @property
    def _max_limit(self) -> int:
        if self._glob_config:
            return self._glob_config.max_limit
        return 5000

    @property
    def _skip_dirs(self) -> set[str]:
        if self._glob_config:
            return set(self._glob_config.skip_dirs)
        return {
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            ".git",
            "dist",
            "build",
            ".next",
            "target",
        }

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute glob search."""
        params = GlobParams(**invocation.params)
        search_path = resolve_path(invocation.cwd, params.path)
        sandbox = get_sandbox()

        if sandbox:
            sandbox_result = sandbox.can_read(search_path)
            if not sandbox_result.allowed:
                return ToolResult.error_result(
                    f"Glob blocked by sandbox: {sandbox_result.reason}",
                    metadata={"violation_type": sandbox_result.violation_type},
                )

        # Validation
        if not search_path.exists():
            return ToolResult.error_result(f"Path does not exist: {search_path}")
        if not search_path.is_dir():
            return ToolResult.error_result(f"Path is not a directory: {search_path}")
        if not params.pattern:
            return ToolResult.error_result("Pattern cannot be empty")

        # Conflicting options
        if params.files_only and params.dirs_only:
            return ToolResult.error_result("Cannot specify both files_only and dirs_only")

        # Apply config limits
        limit = min(params.limit, self._max_limit)

        # Load gitignore if needed
        gitignore_spec = None
        if params.respect_gitignore:
            gitignore_spec = self._load_gitignore(search_path)

        # Compile pattern
        try:
            pattern_regex = self._compile_glob_pattern(params.pattern, case_sensitive=params.case_sensitive)
        except re.error as e:
            return ToolResult.error_result(f"Invalid pattern: {e}")

        # Collect matches
        matches: list[GlobMatch] = []
        total_count = 0

        try:
            for match in self._walk_and_match(
                root=search_path,
                pattern_regex=pattern_regex,
                include_hidden=params.include_hidden,
                gitignore_spec=gitignore_spec,
                files_only=params.files_only,
                dirs_only=params.dirs_only,
                exclude_patterns=params.exclude,
            ):
                total_count += 1
                if len(matches) < limit:
                    matches.append(match)
        except PermissionError as e:
            return ToolResult.error_result(f"Permission denied: {e}")

        # Format output
        output = self._format_output(
            matches=matches,
            root=search_path,
            pattern=params.pattern,
            absolute_paths=params.absolute_paths,
            total_count=total_count,
            limit=limit,
        )

        return ToolResult.success_result(
            output=output,
            metadata={
                "pattern": params.pattern,
                "path": str(search_path),
                "total_matches": total_count,
                "shown_matches": len(matches),
                "truncated": total_count > limit,
            },
        )

    def _compile_glob_pattern(self, pattern: str, case_sensitive: bool) -> re.Pattern:
        """Convert glob pattern to regex."""
        # Handle brace expansion first {a,b,c}
        regex = self._glob_to_regex(pattern)

        # Anchor pattern
        regex = "^" + regex + "$"

        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(regex, flags)

    def _glob_to_regex(self, pattern: str) -> str:
        """Convert glob pattern to regex string."""
        regex = ""
        i = 0
        n = len(pattern)

        while i < n:
            c = pattern[i]

            if c == "*":
                if i + 1 < n and pattern[i + 1] == "*":
                    # ** matches any path segments
                    if i + 2 < n and pattern[i + 2] == "/":
                        regex += "(?:.*/)?"
                        i += 3
                        continue
                    else:
                        regex += ".*"
                        i += 2
                        continue
                else:
                    # * matches within a segment (no slashes)
                    regex += "[^/]*"
            elif c == "?":
                regex += "[^/]"
            elif c == "[":
                # Character class
                j = i + 1
                if j < n and pattern[j] == "!":
                    regex += "[^"
                    j += 1
                elif j < n and pattern[j] == "^":
                    regex += "[^"
                    j += 1
                else:
                    regex += "["
                # Find closing bracket
                while j < n and pattern[j] != "]":
                    if pattern[j] == "\\":
                        regex += "\\\\"
                        j += 1
                        if j < n:
                            regex += pattern[j]
                            j += 1
                    else:
                        regex += pattern[j]
                        j += 1
                regex += "]"
                i = j
            elif c == "{":
                # Brace expansion {a,b,c}
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if pattern[j] == "{":
                        depth += 1
                    elif pattern[j] == "}":
                        depth -= 1
                    j += 1
                if depth == 0:
                    inner = pattern[i + 1 : j - 1]
                    alternatives = self._split_brace_alternatives(inner)
                    regex += "(?:" + "|".join(self._glob_to_regex(alt) for alt in alternatives) + ")"
                    i = j
                    continue
                else:
                    regex += re.escape(c)
            elif c in ".^$+|()":
                regex += "\\" + c
            elif c == "\\":
                # Escape next character
                i += 1
                if i < n:
                    regex += re.escape(pattern[i])
            else:
                regex += re.escape(c)
            i += 1

        return regex

    def _split_brace_alternatives(self, inner: str) -> list[str]:
        """Split brace content by commas, respecting nested braces."""
        alternatives = []
        current = ""
        depth = 0

        for c in inner:
            if c == "{":
                depth += 1
                current += c
            elif c == "}":
                depth -= 1
                current += c
            elif c == "," and depth == 0:
                alternatives.append(current)
                current = ""
            else:
                current += c

        if current:
            alternatives.append(current)

        return alternatives

    def _walk_and_match(
        self,
        root: Path,
        pattern_regex: re.Pattern,
        include_hidden: bool,
        gitignore_spec,
        files_only: bool,
        dirs_only: bool,
        exclude_patterns: list[str],
    ) -> Iterator[GlobMatch]:
        """Walk directory tree and yield matches."""

        def _walk(current: Path) -> Iterator[GlobMatch]:
            try:
                entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
            except PermissionError:
                return

            for entry in entries:
                name = entry.name

                # Skip hidden
                if not include_hidden and name.startswith("."):
                    continue

                # Skip known noise directories
                if entry.is_dir() and name in self._skip_dirs:
                    continue

                # Get relative path for matching
                try:
                    rel_path = entry.relative_to(root)
                    rel_str = str(rel_path).replace("\\", "/")
                except ValueError:
                    continue

                # Check gitignore
                if gitignore_spec:
                    check_path = rel_str + "/" if entry.is_dir() else rel_str
                    if gitignore_spec.match_file(check_path):
                        continue

                # Check exclude patterns
                excluded = False
                for excl in exclude_patterns:
                    if fnmatch.fnmatch(rel_str, excl) or fnmatch.fnmatch(name, excl):
                        excluded = True
                        break
                if excluded:
                    continue

                is_dir = entry.is_dir()

                # Check if matches pattern
                if pattern_regex.match(rel_str):
                    # Filter by type
                    if files_only and is_dir:
                        pass  # Skip directories
                    elif dirs_only and not is_dir:
                        pass  # Skip files
                    else:
                        yield GlobMatch(
                            path=entry,
                            relative_path=rel_str,
                            is_dir=is_dir,
                        )

                # Recurse into directories
                if is_dir:
                    yield from _walk(entry)

        yield from _walk(root)

    def _load_gitignore(self, root: Path):
        """Load .gitignore patterns."""
        try:
            import pathspec
        except ImportError:
            return None

        gitignore_path = root / ".gitignore"
        if not gitignore_path.exists():
            for parent in root.parents:
                candidate = parent / ".gitignore"
                if candidate.exists():
                    gitignore_path = candidate
                    break
                if (parent / ".git").exists():
                    break
            else:
                return None

        if not gitignore_path.exists():
            return None

        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                patterns = f.read().splitlines()
            return pathspec.PathSpec.from_lines("gitignore", patterns)
        except Exception:
            return None

    def _format_output(
        self,
        matches: list[GlobMatch],
        root: Path,
        pattern: str,
        absolute_paths: bool,
        total_count: int,
        limit: int,
    ) -> str:
        """Format output for LLM consumption."""
        if not matches:
            return f'No files found matching "{pattern}" in {root}'

        # Determine type label
        if all(m.is_dir for m in matches):
            type_label = "directories"
        elif any(m.is_dir for m in matches):
            type_label = "items"
        else:
            type_label = "files"

        lines = [f'Found {total_count} {type_label} matching "{pattern}":', ""]

        for match in matches:
            if absolute_paths:
                lines.append(str(match.path))
            else:
                display = match.relative_path
                if match.is_dir:
                    display += "/"
                lines.append(display)

        # Truncation notice
        if total_count > limit:
            lines.append("")
            lines.append(f"Showing {limit} of {total_count} {type_label} (use limit= to see more)")

        return "\n".join(lines)

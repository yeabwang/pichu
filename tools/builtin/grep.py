"""grep/search tool.

Features:
- Ripgrep backend with Python fallback
- Regex and literal pattern support
- Case sensitivity control
- .gitignore respect
- Context lines
- File filtering (include/exclude)
- Binary file detection
- Result limiting
- LLM-optimized output format
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from pydantic import BaseModel, Field

from safety.sandbox import get_sandbox
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from utils.paths import resolve_path

if TYPE_CHECKING:
    from config import Config

logger = logging.getLogger(__name__)


class GrepParams(BaseModel):
    """Parameters for grep tool."""

    pattern: str = Field(
        ...,
        description="Search pattern (literal text or regex)",
    )
    path: str = Field(
        default=".",
        description="File or directory to search",
    )
    is_regex: bool = Field(
        default=False,
        description="Interpret pattern as regex (default: literal text)",
    )
    case_sensitive: bool = Field(
        default=False,
        description="Case-sensitive matching (default: case-insensitive)",
    )
    whole_word: bool = Field(
        default=False,
        description="Match whole words only",
    )
    include: str | None = Field(
        default=None,
        description="Glob pattern for files to include (e.g., '*.py', '*.{ts,tsx}')",
    )
    exclude: list[str] = Field(
        default_factory=list,
        description="Glob patterns to exclude",
    )
    context_lines: int = Field(
        default=0,
        ge=0,
        le=10,
        description="Lines of context before and after match",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=2000,
        description="Maximum matches to return",
    )
    files_only: bool = Field(
        default=False,
        description="Return only matching filenames, not content",
    )
    respect_gitignore: bool = Field(
        default=True,
        description="Honor .gitignore patterns",
    )
    include_hidden: bool = Field(
        default=False,
        description="Include hidden files/directories",
    )


@dataclass
class GrepMatch:
    """Represents a single grep match."""

    file_path: Path
    line_number: int
    line_content: str
    context_before: list[str] = field(default_factory=list)
    context_after: list[str] = field(default_factory=list)


class GrepTool(Tool):
    """Search for patterns in files.

    Uses ripgrep when available for performance,
    falls back to pure Python implementation.
    """

    name = "grep"
    description = (
        "Search for text or patterns in files. Supports regex, case-insensitive "
        "matching, file filtering, and context lines. Uses ripgrep for performance "
        "when available. Respects .gitignore by default."
    )
    kind = ToolKind.READ
    schema = GrepParams

    def __init__(self, config: "Config | None" = None) -> None:
        super().__init__(config)
        self._grep_config = config.grep if config and hasattr(config, "grep") else None
        self._ripgrep_available: bool | None = None

    @property
    def _max_file_size(self) -> int:
        if self._grep_config:
            return self._grep_config.max_file_size
        return 1_000_000  # 1MB

    @property
    def _timeout(self) -> int:
        if self._grep_config:
            return self._grep_config.timeout
        return 30

    @property
    def _max_limit(self) -> int:
        if self._grep_config:
            return self._grep_config.max_limit
        return 2000

    @property
    def _default_excludes(self) -> list[str]:
        if self._grep_config:
            return self._grep_config.default_excludes
        return ["*.min.js", "*.min.css", "*.map", "package-lock.json"]

    @property
    def _binary_extensions(self) -> set[str]:
        if self._grep_config:
            return set(self._grep_config.binary_extensions)
        return {
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".bin",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".ico",
            ".webp",
            ".svg",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".wav",
            ".flac",
            ".zip",
            ".tar",
            ".gz",
            ".7z",
            ".rar",
            ".bz2",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".pyc",
            ".pyo",
            ".class",
            ".o",
            ".obj",
            ".a",
            ".lib",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".otf",
            ".sqlite",
            ".db",
            ".pickle",
            ".pkl",
        }

    def _has_ripgrep(self) -> bool:
        """Check if ripgrep is available."""
        if self._ripgrep_available is None:
            self._ripgrep_available = shutil.which("rg") is not None
        return self._ripgrep_available

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute grep search."""
        params = GrepParams(**invocation.params)
        search_path = resolve_path(invocation.cwd, params.path)
        sandbox = get_sandbox()

        if sandbox:
            sandbox_result = sandbox.can_read(search_path)
            if not sandbox_result.allowed:
                return ToolResult.error_result(
                    f"Grep blocked by sandbox: {sandbox_result.reason}",
                    metadata={"violation_type": sandbox_result.violation_type},
                )

        # Validation
        if not search_path.exists():
            return ToolResult.error_result(f"Path does not exist: {search_path}")

        if not params.pattern:
            return ToolResult.error_result("Search pattern cannot be empty")

        # Apply config limits
        limit = min(params.limit, self._max_limit)

        # Build regex pattern
        try:
            regex = self._build_pattern(
                params.pattern,
                is_regex=params.is_regex,
                case_sensitive=params.case_sensitive,
                whole_word=params.whole_word,
            )
        except re.error as e:
            return ToolResult.error_result(f"Invalid regex pattern: {e}")

        # Execute search
        try:
            if self._has_ripgrep() and search_path.is_dir():
                matches = await self._run_ripgrep(params, search_path, invocation.cwd)
            else:
                matches = await self._run_python_search(params, search_path, regex, invocation.cwd)
        except asyncio.TimeoutError:
            return ToolResult.error_result(f"Search timed out after {self._timeout} seconds")
        except Exception as e:
            return ToolResult.error_result(f"Search failed: {e}")

        # Apply limit
        total_matches = len(matches)
        truncated = total_matches > limit
        matches = matches[:limit]

        # Format output
        if params.files_only:
            output = self._format_files_only(matches, search_path, total_matches)
        else:
            output = self._format_matches(matches, search_path, params.context_lines, total_matches, limit)

        return ToolResult.success_result(
            output=output,
            metadata={
                "pattern": params.pattern,
                "path": str(search_path),
                "total_matches": total_matches,
                "shown_matches": len(matches),
                "files_matched": len(set(m.file_path for m in matches)),
                "truncated": truncated,
            },
        )

    def _build_pattern(
        self,
        pattern: str,
        is_regex: bool,
        case_sensitive: bool,
        whole_word: bool,
    ) -> re.Pattern:
        """Build regex pattern from search parameters."""
        if not is_regex:
            pattern = re.escape(pattern)

        if whole_word:
            pattern = rf"\b{pattern}\b"

        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(pattern, flags)

    async def _run_ripgrep(
        self,
        params: GrepParams,
        search_path: Path,
        cwd: Path,
    ) -> list[GrepMatch]:
        """Run search using ripgrep."""
        cmd = ["rg", "--json", "--line-number"]

        # Case sensitivity
        if not params.case_sensitive:
            cmd.append("--ignore-case")

        # Regex vs literal
        if not params.is_regex:
            cmd.append("--fixed-strings")

        # Whole word
        if params.whole_word:
            cmd.append("--word-regexp")

        # Context lines
        if params.context_lines > 0:
            cmd.extend(["--context", str(params.context_lines)])

        # Hidden files
        if params.include_hidden:
            cmd.append("--hidden")

        # Gitignore
        if not params.respect_gitignore:
            cmd.append("--no-ignore")

        # Include pattern
        if params.include:
            cmd.extend(["--glob", params.include])

        # Exclude patterns
        for exclude in params.exclude + self._default_excludes:
            cmd.extend(["--glob", f"!{exclude}"])

        # Max file size
        cmd.extend(["--max-filesize", str(self._max_file_size)])

        # Pattern and path
        cmd.extend(["--", params.pattern, str(search_path)])

        # Run ripgrep
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(cwd),
                ),
                timeout=self._timeout,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.communicate(), timeout=2)
                except Exception as exc:
                    logger.debug("Failed to terminate ripgrep process cleanly: %s", exc)
            raise

        # Parse JSON output
        import json

        matches = []

        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                data = json.loads(line)
                if data.get("type") == "match":
                    match_data = data["data"]
                    file_path = Path(match_data["path"]["text"])
                    line_text = match_data["lines"]["text"].rstrip("\n\r")
                    matches.append(
                        GrepMatch(
                            file_path=file_path,
                            line_number=match_data["line_number"],
                            line_content=line_text,
                        )
                    )
            except (json.JSONDecodeError, KeyError):
                continue

        return matches

    async def _run_python_search(
        self,
        params: GrepParams,
        search_path: Path,
        regex: re.Pattern,
        cwd: Path,
    ) -> list[GrepMatch]:
        """Run search using pure Python (fallback)."""
        matches: list[GrepMatch] = []

        # Load gitignore if needed
        gitignore_spec = None
        if params.respect_gitignore:
            gitignore_spec = self._load_gitignore(search_path if search_path.is_dir() else search_path.parent)

        # Search files
        if search_path.is_file():
            files = [search_path]
        else:
            files = list(
                self._walk_files(
                    search_path,
                    include_hidden=params.include_hidden,
                    include_pattern=params.include,
                    exclude_patterns=params.exclude + self._default_excludes,
                    gitignore_spec=gitignore_spec,
                )
            )

        for file_path in files:
            file_matches = self._search_file(file_path, regex, params.context_lines)
            matches.extend(file_matches)

            # Early exit if we have way more than needed
            if len(matches) >= params.limit * 2:
                break

        return matches

    def _walk_files(
        self,
        root: Path,
        include_hidden: bool,
        include_pattern: str | None,
        exclude_patterns: list[str],
        gitignore_spec,
    ) -> Iterator[Path]:
        """Walk directory and yield matching files."""
        try:
            for entry in root.iterdir():
                name = entry.name

                # Skip hidden
                if not include_hidden and name.startswith("."):
                    continue

                # Skip binary by extension
                if entry.is_file() and entry.suffix.lower() in self._binary_extensions:
                    continue

                # Check gitignore
                if gitignore_spec:
                    try:
                        rel = entry.relative_to(root)
                        rel_str = str(rel).replace("\\", "/")
                        if entry.is_dir():
                            rel_str += "/"
                        if gitignore_spec.match_file(rel_str):
                            continue
                    except ValueError:
                        pass

                # Check excludes
                excluded = False
                for pattern in exclude_patterns:
                    if fnmatch.fnmatch(name, pattern):
                        excluded = True
                        break
                if excluded:
                    continue

                if entry.is_file():
                    # Check include pattern
                    if include_pattern and not fnmatch.fnmatch(name, include_pattern):
                        continue
                    # Check file size
                    try:
                        if entry.stat().st_size > self._max_file_size:
                            continue
                    except OSError:
                        continue
                    yield entry
                elif entry.is_dir():
                    yield from self._walk_files(
                        entry,
                        include_hidden,
                        include_pattern,
                        exclude_patterns,
                        gitignore_spec,
                    )
        except PermissionError:
            pass

    def _search_file(
        self,
        file_path: Path,
        regex: re.Pattern,
        context_lines: int,
    ) -> list[GrepMatch]:
        """Search a single file for matches."""
        matches: list[GrepMatch] = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return matches

        lines = content.splitlines()

        for i, line in enumerate(lines):
            if regex.search(line):
                line_number = i + 1  # 1-indexed

                # Get context
                ctx_before: list[str] = []
                ctx_after: list[str] = []
                if context_lines > 0:
                    start = max(0, i - context_lines)
                    ctx_before = lines[start:i]
                    ctx_after = lines[i + 1 : i + 1 + context_lines]

                matches.append(
                    GrepMatch(
                        file_path=file_path,
                        line_number=line_number,
                        line_content=line,
                        context_before=ctx_before,
                        context_after=ctx_after,
                    )
                )

        return matches

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

    def _format_matches(
        self,
        matches: list[GrepMatch],
        root: Path,
        context_lines: int,
        total_count: int,
        limit: int,
    ) -> str:
        """Format matches for LLM consumption."""
        if not matches:
            return f"No matches found in {root}"

        # Group by file
        by_file: dict[Path, list[GrepMatch]] = {}
        for m in matches:
            by_file.setdefault(m.file_path, []).append(m)

        files_count = len(by_file)
        lines = [
            f"Found {total_count} match{'es' if total_count != 1 else ''} "
            f"in {files_count} file{'s' if files_count != 1 else ''}:",
            "",
        ]

        for file_path, file_matches in by_file.items():
            # Show relative path
            try:
                rel_path = file_path.relative_to(root) if root.is_dir() else file_path.name
            except ValueError:
                rel_path = file_path

            lines.append(str(rel_path))

            for m in file_matches:
                # Context before
                if context_lines > 0 and m.context_before:
                    for j, ctx in enumerate(m.context_before):
                        ctx_line = m.line_number - len(m.context_before) + j
                        lines.append(f"  {ctx_line:4d}- {ctx}")

                # Match line
                lines.append(f"  {m.line_number:4d}: {m.line_content}")

                # Context after
                if context_lines > 0 and m.context_after:
                    for j, ctx in enumerate(m.context_after):
                        ctx_line = m.line_number + j + 1
                        lines.append(f"  {ctx_line:4d}- {ctx}")

            lines.append("")

        # Truncation notice
        if total_count > limit:
            lines.append(f"Showing {limit} of {total_count} matches (use limit= to see more)")

        return "\n".join(lines)

    def _format_files_only(
        self,
        matches: list[GrepMatch],
        root: Path,
        total_count: int,
    ) -> str:
        """Format output for files-only mode."""
        unique_files = sorted(set(m.file_path for m in matches))

        if not unique_files:
            return f"No files match the pattern in {root}"

        lines = [
            f"Found {len(unique_files)} file{'s' if len(unique_files) != 1 else ''} with matches:",
            "",
        ]

        for file_path in unique_files:
            try:
                rel_path = file_path.relative_to(root) if root.is_dir() else file_path.name
            except ValueError:
                rel_path = file_path
            lines.append(str(rel_path))

        return "\n".join(lines)

"""Directory listing tool.

Features:
- Recursive tree traversal with configurable depth
- Pagination (offset/limit) for large directories
- .gitignore respect
- Auto-skip noise directories (node_modules, __pycache__, etc.)
- Symlink detection
- Optional file sizes
- Token-efficient indented tree output
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from pydantic import BaseModel, Field
from safety.sandbox import get_sandbox
from utils.paths import resolve_path

from tools.base import Tool, ToolInvocation, ToolKind, ToolResult

if TYPE_CHECKING:
    from config import Config

    try:
        import pathspec
    except ImportError:
        pathspec = None  # type: ignore[assignment]


class ListDirParams(BaseModel):
    """Parameters for list_dir tool."""

    path: str = Field(
        default=".",
        description="Directory path to list (relative or absolute)",
    )
    depth: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Max directory depth (1=current only, 2=one level down, etc.)",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum entries to return",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Entries to skip (for pagination)",
    )
    include_hidden: bool = Field(
        default=False,
        description="Include hidden files (starting with .)",
    )
    respect_gitignore: bool = Field(
        default=True,
        description="Respect .gitignore patterns",
    )
    include_size: bool = Field(
        default=False,
        description="Include file sizes in output",
    )


@dataclass
class DirEntry:
    """Represents a directory entry."""

    path: Path
    relative_path: str
    depth: int
    is_dir: bool
    is_symlink: bool
    size: int | None = None


class ListDirTool(Tool):
    """List directory contents as an indented tree.

    Features:
    - Recursive depth control
    - Pagination for large directories
    - .gitignore filtering
    - Auto-skips noise directories (node_modules, __pycache__, .git, etc.)
    - Symlink detection
    - Optional file sizes
    """

    name = "list_dir"
    description = (
        "List directory contents as an indented tree. Supports recursive depth, "
        "pagination, .gitignore filtering, and auto-skips noise directories "
        "(node_modules, __pycache__, .git, etc.). Use depth=1 for current directory only."
    )
    kind = ToolKind.READ
    schema = ListDirParams

    def __init__(self, config: "Config | None" = None) -> None:
        super().__init__(config)
        self._list_dir_config = config.list_dir if config else None

    @property
    def _skip_dirs(self) -> set[str]:
        """Get directories to skip from config."""
        if self._list_dir_config:
            return set(self._list_dir_config.skip_dirs)
        # Fallback defaults
        return {
            "node_modules",
            "__pycache__",
            ".venv",
            "venv",
            ".git",
            "dist",
            "build",
            ".next",
            ".idea",
        }

    @property
    def _default_depth(self) -> int:
        if self._list_dir_config:
            return self._list_dir_config.default_depth
        return 2

    @property
    def _max_depth(self) -> int:
        if self._list_dir_config:
            return self._list_dir_config.max_depth
        return 5

    @property
    def _default_limit(self) -> int:
        if self._list_dir_config:
            return self._list_dir_config.default_limit
        return 100

    @property
    def _max_limit(self) -> int:
        if self._list_dir_config:
            return self._list_dir_config.max_limit
        return 500

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute the list_dir tool."""
        params = ListDirParams(**invocation.params)
        root = resolve_path(invocation.cwd, params.path)
        sandbox = get_sandbox()

        if sandbox:
            sandbox_result = sandbox.can_read(root)
            if not sandbox_result.allowed:
                return ToolResult.error_result(
                    f"List blocked by sandbox: {sandbox_result.reason}",
                    metadata={"violation_type": sandbox_result.violation_type},
                )

        # Validation
        if not root.exists():
            return ToolResult.error_result(f"Path does not exist: {root}")
        if not root.is_dir():
            return ToolResult.error_result(f"Path is not a directory: {root}")

        # Apply config limits
        depth = min(params.depth, self._max_depth)
        limit = min(params.limit, self._max_limit)

        # Load gitignore if requested
        gitignore_spec = None
        if params.respect_gitignore:
            gitignore_spec = self._load_gitignore(root)

        # Collect entries
        all_entries: list[DirEntry] = []
        try:
            for entry in self._walk_directory(
                root=root,
                max_depth=depth,
                include_hidden=params.include_hidden,
                gitignore_spec=gitignore_spec,
                include_size=params.include_size,
            ):
                all_entries.append(entry)
        except PermissionError as e:
            return ToolResult.error_result(f"Permission denied: {e}")

        total_count = len(all_entries)

        # Apply pagination
        paginated = all_entries[params.offset : params.offset + limit]

        if not paginated and total_count == 0:
            return ToolResult.success_result(
                output=f"Directory is empty: {root}",
                metadata={"path": str(root), "total_entries": 0, "shown_entries": 0},
            )

        # Format output
        output = self._format_output(
            entries=paginated,
            root=root,
            include_size=params.include_size,
            total_count=total_count,
            offset=params.offset,
            limit=limit,
        )

        return ToolResult.success_result(
            output=output,
            metadata={
                "path": str(root),
                "total_entries": total_count,
                "shown_entries": len(paginated),
                "offset": params.offset,
                "depth": depth,
            },
        )

    def _load_gitignore(self, root: Path):
        """Load .gitignore patterns from root or parent directories."""
        try:
            import pathspec
        except ImportError:
            return None  # Graceful degradation

        # Find .gitignore file
        gitignore_path = root / ".gitignore"
        if not gitignore_path.exists():
            # Check parent directories up to git root
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

    def _should_skip_dir(self, name: str) -> bool:
        """Check if directory should be skipped based on config patterns."""
        for pattern in self._skip_dirs:
            # Support glob patterns like *.egg-info
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _walk_directory(
        self,
        root: Path,
        max_depth: int,
        include_hidden: bool,
        gitignore_spec,
        include_size: bool,
    ) -> Iterator[DirEntry]:
        """Recursively walk directory with filtering."""

        def _walk(current: Path, depth: int) -> Iterator[DirEntry]:
            if depth > max_depth:
                return

            try:
                # Sort: directories first, then alphabetically (case-insensitive)
                entries = sorted(
                    current.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except PermissionError:
                return

            for entry in entries:
                name = entry.name

                # Skip hidden files unless included
                if not include_hidden and name.startswith("."):
                    continue

                # Skip noise directories
                if entry.is_dir() and self._should_skip_dir(name):
                    continue

                # Check gitignore
                if gitignore_spec:
                    try:
                        relative = entry.relative_to(root)
                        rel_str = str(relative).replace("\\", "/")
                        if entry.is_dir():
                            rel_str += "/"
                        if gitignore_spec.match_file(rel_str):
                            continue
                    except ValueError:
                        pass

                is_symlink = entry.is_symlink()
                is_dir = entry.is_dir()

                # Get size if requested (only for files)
                size = None
                if include_size and not is_dir:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        pass

                yield DirEntry(
                    path=entry,
                    relative_path=str(entry.relative_to(root)),
                    depth=depth,
                    is_dir=is_dir,
                    is_symlink=is_symlink,
                    size=size,
                )

                # Recurse into directories (skip symlinks to avoid loops)
                if is_dir and not is_symlink:
                    yield from _walk(entry, depth + 1)

        yield from _walk(root, 1)

    def _format_output(
        self,
        entries: list[DirEntry],
        root: Path,
        include_size: bool,
        total_count: int,
        offset: int,
        limit: int,
    ) -> str:
        """Format entries as indented tree."""
        lines = [f"📁 {root}", ""]

        for entry in entries:
            indent = "  " * (entry.depth - 1)

            # Determine suffix
            if entry.is_dir:
                suffix = "/"
            elif entry.is_symlink:
                suffix = " → (symlink)"
            else:
                suffix = ""

            name = entry.path.name + suffix

            # Add size if requested
            if include_size and entry.size is not None:
                size_str = self._format_size(entry.size)
                lines.append(f"{indent}{name}  [{size_str}]")
            else:
                lines.append(f"{indent}{name}")

        # Pagination info
        shown = len(entries)
        if offset > 0 or shown < total_count:
            lines.append("")
            end_idx = offset + shown
            lines.append(f"Showing {offset + 1}-{end_idx} of {total_count} entries")
            if end_idx < total_count:
                lines.append(f"Use offset={end_idx} to see more")

        # Large directory warning
        if total_count > 500:
            lines.append("")
            lines.append("⚠ Large directory - use a more specific path or increase depth gradually")

        return "\n".join(lines)

    @staticmethod
    def _format_size(size: int | float) -> str:
        """Format file size in human-readable form."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                if unit == "B":
                    return f"{size}B"
                return f"{size:.1f}{unit}"
            size = size / 1024
        return f"{size:.1f}TB"

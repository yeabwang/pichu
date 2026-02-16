from pydantic import BaseModel, Field
from safety.sandbox import get_sandbox
from utils.paths import ensure_parent_directory, resolve_path

from tools.base import (
    FileDiff,
    Tool,
    ToolConfirmation,
    ToolInvocation,
    ToolKind,
    ToolResult,
)


class WriteFileParams(BaseModel):
    path: str = Field(
        ...,
        description="Path to the file to write (relative to working directory or absolute)",
    )
    content: str = Field(..., description="Content to write to the file")
    create_directories: bool = Field(True, description="Create parent directories if they don't exist")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Write content to a file. Creates the file if it doesn't exist, "
        "or overwrites if it does. Parent directories are created automatically. "
        "Use this for creating new files or completely replacing file contents. "
        "For partial modifications, use the edit tool instead."
    )
    kind = ToolKind.WRITE
    schema = WriteFileParams

    def __init__(self, config=None):
        super().__init__(config)
        self._checkpoint_manager = None

    def set_checkpoint_manager(self, manager) -> None:
        """Inject checkpoint manager for snapshotting files before writes."""
        self._checkpoint_manager = manager

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        params = WriteFileParams(**invocation.params)
        file_path = resolve_path(invocation.cwd, params.path)
        is_new_file = not file_path.exists()

        old_content = ""
        if not is_new_file:
            try:
                old_content = file_path.read_text(encoding="utf-8")
            except (OSError, IOError):
                old_content = ""

        diff = FileDiff(
            file_path=str(file_path),
            old_content=old_content,
            new_content=params.content,
            is_new_file=is_new_file,
        )

        action = "Create" if is_new_file else "Overwrite"
        is_outside_cwd = not file_path.is_relative_to(invocation.cwd)

        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=f"{action} file: {file_path}",
            diff=diff,
            affected_paths=[str(file_path)],
            is_dangerous=is_outside_cwd,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = WriteFileParams(**invocation.params)
        file_path = resolve_path(invocation.cwd, params.path)

        # Sandbox check - restrict writes to allowed directories
        sandbox = get_sandbox()
        if sandbox:
            result = sandbox.can_write(file_path)
            if not result.allowed:
                return ToolResult.error_result(f"Write blocked by sandbox: {result.reason}")

        is_new_file = not file_path.exists()
        old_content = ""

        if not is_new_file:
            try:
                old_content = file_path.read_text(encoding="utf-8")
            except (OSError, IOError) as e:
                return ToolResult.error_result(f"Failed to read existing file {file_path}: {e}")
        try:
            if params.create_directories:
                ensure_parent_directory(file_path)
            elif not file_path.parent.exists():
                return ToolResult.error_result(f"Parent directory does not exist for {file_path.parent}..")

            # Snapshot file before writing (for checkpoint/rewind)
            if self._checkpoint_manager:
                self._checkpoint_manager.snapshot_file(str(file_path))

            file_path.write_text(params.content, encoding="utf-8")
            action = "Created" if is_new_file else "Updated"
            line_count = len(params.content.splitlines())

            return ToolResult.success_result(
                output=f"{action}: {file_path} : {line_count} lines written.",
                diff=FileDiff(
                    file_path=str(file_path),
                    old_content=old_content,
                    new_content=params.content,
                    is_new_file=is_new_file,
                ),
                metadata={
                    "file_path": str(file_path),
                    "is_new_file": is_new_file,
                    "lines": line_count,
                    "bytes": len(params.content.encode("utf-8")),
                    "old_content": old_content,
                },
            )
        except OSError as e:
            return ToolResult.error_result(f"Failed to write to file {file_path}: {e}")

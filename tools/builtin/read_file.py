from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from safety.sandbox import get_sandbox
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from utils.paths import is_binary_file, resolve_path
from utils.text import count_tokens, truncate_text_to_token_limit

if TYPE_CHECKING:
    from config import Config


class ReadFileParams(BaseModel):
    file_path: str = Field(..., description="The path to the file to be read. abs_path or rel_path.")

    offset: int = Field(
        1,
        ge=1,
        description="The offset from the start of the file.(1-based) and defaults to 1",
    )

    limit: int | None = Field(
        None,
        ge=1,
        description="The maximum number of lines to read from the file. If not specified, read until the end of the file. ",
    )


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read the content of a text file. Returns the file content with line numbers. "
        "For large files, use offset and limit to read specific sections. "
        "Can't read binary files (images, executables, etc.)."
    )
    kind = ToolKind.READ
    schema = ReadFileParams

    def __init__(self, config: Config | None = None) -> None:
        super().__init__(config)

    @property
    def max_file_size(self) -> int:
        """Get max file size from config or use default."""
        if self._config:
            return self._config.limits.max_file_size
        return 10 * 1024 * 1024  # 10 MB fallback

    @property
    def max_output_tokens(self) -> int:
        """Get max output tokens from config or use default."""
        if self._config:
            return self._config.limits.max_tool_output_tokens
        return 50_000  # fallback

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = ReadFileParams(**invocation.params)
        file_path = resolve_path(invocation.cwd, params.file_path)
        sandbox = get_sandbox()

        if sandbox:
            sandbox_result = sandbox.can_read(file_path)
            if not sandbox_result.allowed:
                return ToolResult.error_result(
                    f"Read blocked by sandbox: {sandbox_result.reason}",
                    metadata={"violation_type": sandbox_result.violation_type},
                )

        if not file_path.exists():
            return ToolResult.error_result(f"File not found: {file_path}")
        if not file_path.is_file():
            return ToolResult.error_result(f"Path is not a file: {file_path}")

        file_size = file_path.stat().st_size

        if file_size > self.max_file_size:
            return ToolResult.error_result(
                f"File size exceeds limit of {self.max_file_size / (1024 * 1024)} MB: {file_size / (1024 * 1024):.2f} MB"
            )

        if is_binary_file(file_path):
            return ToolResult.error_result(f"Cannot read binary file: {file_path}")

        if params.offset < 1:
            return ToolResult.error_result("Offset must be greater than or equal to 1.")
        try:
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="latin-1", errors="replace")
            except Exception as e:
                return ToolResult.error_result(f"Error reading file {file_path}: {str(e)}")

            lines = content.splitlines()
            total_lines = len(lines)

            if total_lines == 0:
                return ToolResult.success_result(
                    output="Empty file.",
                    metadata={"total_lines": 0},
                )

            start_index = max(0, params.offset - 1)

            if params.limit is not None:
                end_index = min(start_index + params.limit, total_lines)
            else:
                end_index = total_lines

            selected_lines = lines[start_index:end_index]

            formatted_lines = []

            for i, line in enumerate(selected_lines, start=start_index + 1):
                formatted_lines.append(f"{i:6}: {line}")

            output = "\n".join(formatted_lines)
            model_name = self._config.model if self._config else "gpt-4"
            token_count = count_tokens(output, model_name)
            truncated = False
            actual_shown_end = end_index

            if token_count > self.max_output_tokens:
                output = truncate_text_to_token_limit(
                    output,
                    self.max_output_tokens,
                    suffix=f"\n...[truncated {total_lines} total lines]",
                )
                truncated = True
                # Find actual last line number in truncated output
                import re

                for line in reversed(output.splitlines()):
                    match = re.match(r"^\s*(\d+):", line)
                    if match:
                        actual_shown_end = int(match.group(1))
                        break

            metadata_lines = []

            if start_index > 0 or end_index < total_lines:
                metadata_lines.append(f"Showing lines: {start_index + 1}-{actual_shown_end} of {total_lines}")

            if metadata_lines:
                header = " | ".join(metadata_lines) + "\n\n"
                output = header + output

            return ToolResult.success_result(
                output=output,
                truncated=truncated,
                metadata={
                    "path": str(file_path),
                    "total_lines": total_lines,
                    "shown_start_line": start_index + 1,
                    "shown_end_line": actual_shown_end,
                },
            )
        except Exception as e:
            return ToolResult.error_result(f"Failed to read the file: {str(e)}")

from pathlib import Path

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


class EditParams(BaseModel):
    path: str = Field(
        ...,
        description="Path to the file to edit (relative to working directory or absolute path)",
    )
    old_string: str = Field(
        "",
        description="The exact text to find and replace. Must match exactly including all whitespace and indentation. For new files, leave this empty.",
    )
    new_string: str = Field(
        ...,
        description="The text to replace old_string with. Can be empty to delete text",
    )
    replace_all: bool = Field(False, description="Replace all occurrences of old_string (default: false)")


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit a file by replacing text. The old_string must match exactly "
        "(including whitespace and indentation) and must be unique in the file "
        "unless replace_all is true. Use this for precise, surgical edits. "
        "Can also create new files if the file doesn't exist and old_string is empty. "
        "For complete rewrites of existing files, use write_file instead."
    )
    kind = ToolKind.WRITE
    schema = EditParams

    def __init__(self, config=None):
        super().__init__(config)
        self._checkpoint_manager = None

    def set_checkpoint_manager(self, manager) -> None:
        """Inject checkpoint manager for snapshotting files before edits."""
        self._checkpoint_manager = manager

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        params = EditParams(**invocation.params)
        file_path = resolve_path(invocation.cwd, params.path)
        is_new_file = not file_path.exists()

        old_content = ""
        if not is_new_file:
            try:
                old_content = file_path.read_text(encoding="utf-8")
            except (OSError, IOError):
                old_content = ""

        # Build the expected new content for the diff
        if is_new_file:
            new_content = params.new_string
        elif params.old_string:
            new_content = old_content.replace(params.old_string, params.new_string, 1)
        else:
            new_content = params.new_string

        diff = FileDiff(
            file_path=str(file_path),
            old_content=old_content,
            new_content=new_content,
            is_new_file=is_new_file,
        )

        action = "Create" if is_new_file else "Edit"
        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=f"{action} file: {file_path}",
            diff=diff,
            affected_paths=[str(file_path)],
            is_dangerous=(not file_path.is_relative_to(invocation.cwd) if not is_new_file else False),
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = EditParams(**invocation.params)
        file_path = resolve_path(invocation.cwd, params.path)

        # Sandbox check - restrict writes to allowed directories
        sandbox = get_sandbox()
        if sandbox:
            result = sandbox.can_write(file_path)
            if not result.allowed:
                return ToolResult.error_result(error_message=f"Edit blocked by sandbox: {result.reason}")

        if not file_path.exists():
            if params.old_string:
                return ToolResult.error_result(
                    error_message=f"File does not exist: {file_path}. To create a new file, use an empty old_string.",
                )
            else:
                # Creating a new file — snapshot (file doesn't exist yet)
                if self._checkpoint_manager:
                    self._checkpoint_manager.snapshot_file(str(file_path))
                try:
                    ensure_parent_directory(file_path)
                    file_path.write_text(params.new_string, encoding="utf-8")
                except (OSError, IOError) as e:
                    return ToolResult.error_result(
                        error_message=f"Failed to create file {file_path}: {e}",
                    )

                line_count = len(params.new_string.splitlines())
                diff = FileDiff(
                    file_path=str(file_path),
                    old_content="",
                    new_content=params.new_string,
                    is_new_file=True,
                )
                meta_data = {
                    "file_path": str(file_path),
                    "total_lines": line_count,
                    "is_new_file": True,
                }

                return ToolResult.success_result(
                    output=f"Created new file {file_path}.",
                    diff=diff,
                    metadata=meta_data,
                )

        try:
            old_content = file_path.read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            return ToolResult.error_result(
                error_message=f"Failed to read file {file_path}: {e}",
            )

        if not params.old_string:
            return ToolResult.error_result(
                error_message="old_string cannot be empty when editing an existing file. Provide old_string to edit or use write_file to overwrite the file.",
            )

        occurrences = old_content.count(params.old_string)
        if occurrences == 0:
            return self._no_match_error(params.old_string, old_content, file_path)

        if occurrences > 1 and not params.replace_all:
            return ToolResult.error_result(
                error_message=f"old_string occurs {occurrences} times in {file_path}. 1. Set replace_all to true to replace all occurrences.\n or 2. Provide a more specific old_string that matches only once.",
                metadata={"occurrences": occurrences},
            )

        if params.replace_all:
            new_content = old_content.replace(params.old_string, params.new_string)
            replaced_count = occurrences
        else:
            new_content = old_content.replace(params.old_string, params.new_string, 1)
            replaced_count = 1

        if new_content == old_content:
            return ToolResult.error_result(
                error_message="No changes made to the file after replacement.",
            )

        try:
            # Snapshot file before writing (for checkpoint/rewind)
            if self._checkpoint_manager:
                self._checkpoint_manager.snapshot_file(str(file_path))
            file_path.write_text(new_content, encoding="utf-8")
            new_lines = len(new_content.splitlines())
            old_lines = len(old_content.splitlines())
            line_diff = new_lines - old_lines
            if line_diff > 0:
                diff_message = f"+{line_diff} lines"
            elif line_diff < 0:
                diff_message = f"{line_diff} lines"
            else:
                diff_message = "no line count change"

            diff = FileDiff(
                file_path=str(file_path),
                old_content=old_content,
                new_content=new_content,
                is_new_file=False,
            )
            meta_data = {
                "file_path": str(file_path),
                "total_lines": new_lines,
                "lines_added": max(0, line_diff),
                "lines_removed": max(0, -line_diff),
                "replacements_made": replaced_count,
            }

            return ToolResult.success_result(
                output=f"Edited file {file_path}: {replaced_count} replacements made. ({diff_message})",
                diff=diff,
                metadata=meta_data,
            )

        except (OSError, IOError) as e:
            return ToolResult.error_result(
                error_message=f"Failed to write to file {file_path}: {e}",
            )

    def _no_match_error(self, old_string: str, content: str, file_path: Path) -> ToolResult:
        lines = content.splitlines()
        partial_matches = []
        search_terms = old_string.strip().split()[:5]  # First few words for context

        if search_terms:
            first_term = search_terms[0]
            for i, line in enumerate(lines, 1):
                if first_term in line:
                    partial_matches.append((i, line.strip()[:80]))  # Line number and snippet
                    if len(partial_matches) >= 3:
                        break

        error_msg = f"old_string not found in {file_path}."

        if partial_matches:
            error_msg += " Partial matches found:\n"
            for line_no, snippet in partial_matches:
                error_msg += f"  Line {line_no}: {snippet}\n"
            error_msg += " Please ensure old_string matches exactly, including whitespace and indentation."
        else:
            error_msg += (
                "Make sure the text matches exactly, including\n"
                "- whitespace and indentation.\n"
                "- Line breaks.\n"
                "- Case sensitivity.\n"
                "- Any invisible characters.\n"
                "- Try re-reading the file using read_file tool and then editing again.\n"
            )

        return ToolResult.error_result(error_message=error_msg)

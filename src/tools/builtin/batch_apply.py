from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from safety.sandbox import get_sandbox
from tools.base import (
    BatchFileDiff,
    FileDiff,
    OperationResult,
    Tool,
    ToolConfirmation,
    ToolInvocation,
    ToolKind,
    ToolResult,
)
from utils.paths import ensure_parent_directory, resolve_path


class EditOperationParams(BaseModel):
    path: str = Field(
        ...,
        description="File path (relative to working directory or absolute)",
    )
    old_string: str = Field(
        ...,
        description="Exact text to find and replace. Must match exactly including whitespace.",
    )
    new_string: str = Field(
        ...,
        description="Replacement text. Can be empty to delete text.",
    )
    description: str = Field(
        "",
        description="Brief explanation of what this edit does",
    )


class BatchApplyParams(BaseModel):
    edits: list[EditOperationParams] = Field(
        ...,
        min_length=1,
        description="Array of edit operations to apply sequentially",
    )
    stop_on_error: bool = Field(
        False,
        description="Stop processing remaining edits if any edit fails (default: continue)",
    )
    description: str = Field(
        "",
        description="Overall description of what this batch accomplishes",
    )


class BatchApplyTool(Tool):
    name = "batch_apply"
    description = (
        "Apply multiple text edits across one or more files in a single operation. "
        "More efficient than multiple edit_file calls. Each edit specifies a file path, "
        "the exact text to find (old_string), and the replacement text (new_string). "
        "Edits to the same file are applied sequentially, so later edits see the results "
        "of earlier edits. Use this for refactoring, bulk changes, or multi-file modifications."
    )
    kind = ToolKind.WRITE
    schema = BatchApplyParams

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        params = BatchApplyParams(**invocation.params)
        affected = list({e.path for e in params.edits})
        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=f"Batch edit {len(params.edits)} operations across {len(affected)} file(s)",
            affected_paths=affected,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = BatchApplyParams(**invocation.params)
        sandbox = get_sandbox()

        # Group edits by file path for sequential processing
        edits_by_file: dict[Path, list[tuple[int, EditOperationParams]]] = defaultdict(list)
        for idx, edit in enumerate(params.edits):
            file_path = resolve_path(invocation.cwd, edit.path)
            if sandbox:
                sandbox_result = sandbox.can_write(file_path)
                if not sandbox_result.allowed:
                    return ToolResult.error_result(
                        f"Batch apply blocked by sandbox: {sandbox_result.reason}",
                        metadata={
                            "file_path": str(file_path),
                            "violation_type": sandbox_result.violation_type,
                        },
                    )
            edits_by_file[file_path].append((idx, edit))

        # Results tracking
        operation_results: list[OperationResult] = []
        batch_diff = BatchFileDiff()
        files_modified: list[str] = []
        total_success = 0
        total_failed = 0
        should_stop = False

        # Process each file
        for file_path, file_edits in edits_by_file.items():
            if should_stop:
                # Mark remaining operations as skipped
                for idx, edit in file_edits:
                    operation_results.append(
                        OperationResult(
                            operation_index=idx,
                            path=str(file_path),
                            success=False,
                            message="Skipped due to previous error",
                            error="stop_on_error triggered",
                        )
                    )
                    total_failed += 1
                continue

            # Process file
            file_result = await self._process_file(file_path, file_edits)

            # Collect results
            operation_results.extend(file_result["operation_results"])

            if file_result["diff"]:
                batch_diff.add(file_result["diff"])
                files_modified.append(str(file_path))

            total_success += file_result["success_count"]
            total_failed += file_result["fail_count"]

            if file_result["has_error"] and params.stop_on_error:
                should_stop = True

        # Sort operation results by original index
        operation_results.sort(key=lambda r: r.operation_index)

        # Build output summary
        total_edits = len(params.edits)
        overall_success = total_failed == 0

        if overall_success:
            summary = f"✓ {total_success}/{total_edits} edits applied successfully"
            if len(files_modified) > 0:
                summary += f" across {len(files_modified)} file(s)"
        else:
            summary = f"✗ {total_success}/{total_edits} edits applied, {total_failed} failed"

        # Add per-file summary
        file_summaries = []
        for modified_path in files_modified:
            edits_in_file = sum(1 for r in operation_results if r.path == modified_path and r.success)
            file_summaries.append(f"  • {modified_path}: {edits_in_file} edit(s)")

        if file_summaries:
            summary += "\n\nFiles modified:\n" + "\n".join(file_summaries)

        # Add error details if any
        errors = [r for r in operation_results if not r.success]
        if errors:
            summary += "\n\nErrors:"
            for err in errors[:5]:  # Limit to first 5 errors
                summary += f"\n  ✗ Edit #{err.operation_index + 1} ({err.path}): {err.error}"
            if len(errors) > 5:
                summary += f"\n  ... and {len(errors) - 5} more errors"

        return ToolResult(
            success=overall_success,
            output=summary,
            error=None if overall_success else f"{total_failed} edit(s) failed",
            batch_diff=batch_diff if batch_diff.diffs else None,
            operation_results=operation_results,
            metadata={
                "total_edits": total_edits,
                "successful_edits": total_success,
                "failed_edits": total_failed,
                "files_modified": files_modified,
                "description": params.description,
            },
        )

    async def _process_file(
        self,
        file_path: Path,
        edits: list[tuple[int, EditOperationParams]],
    ) -> dict:
        """Process all edits for a single file."""
        results: list[OperationResult] = []
        success_count = 0
        fail_count = 0
        has_error = False

        # Check if file exists
        is_new_file = not file_path.exists()

        if is_new_file:
            # For new files, only allow if first edit has empty old_string
            first_idx, first_edit = edits[0]
            if first_edit.old_string:
                for idx, edit in edits:
                    results.append(
                        OperationResult(
                            operation_index=idx,
                            path=str(file_path),
                            success=False,
                            message="File does not exist",
                            error=f"File {file_path} does not exist. Use empty old_string to create.",
                        )
                    )
                    fail_count += 1
                    has_error = True
                return {
                    "operation_results": results,
                    "diff": None,
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "has_error": has_error,
                }
            else:
                # Create new file with first edit's new_string
                old_content = ""
                current_content = first_edit.new_string

                results.append(
                    OperationResult(
                        operation_index=first_idx,
                        path=str(file_path),
                        success=True,
                        message="Created new file",
                    )
                )
                success_count += 1
                edits = edits[1:]  # Process remaining edits
        else:
            # Read existing file
            try:
                old_content = file_path.read_text(encoding="utf-8")
                current_content = old_content
            except (OSError, IOError) as e:
                for idx, edit in edits:
                    results.append(
                        OperationResult(
                            operation_index=idx,
                            path=str(file_path),
                            success=False,
                            message="Failed to read file",
                            error=str(e),
                        )
                    )
                    fail_count += 1
                    has_error = True
                return {
                    "operation_results": results,
                    "diff": None,
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "has_error": has_error,
                }

        # Apply each edit sequentially
        for idx, edit in edits:
            if not edit.old_string:
                # Empty old_string on existing file is an error
                results.append(
                    OperationResult(
                        operation_index=idx,
                        path=str(file_path),
                        success=False,
                        message="Invalid edit",
                        error="old_string cannot be empty when editing existing content",
                    )
                )
                fail_count += 1
                has_error = True
                continue

            # Check if old_string exists in current content
            occurrences = current_content.count(edit.old_string)

            if occurrences == 0:
                # Try to find partial matches for helpful error
                error_msg = self._build_no_match_error(edit.old_string, current_content)
                results.append(
                    OperationResult(
                        operation_index=idx,
                        path=str(file_path),
                        success=False,
                        message="old_string not found",
                        error=error_msg,
                    )
                )
                fail_count += 1
                has_error = True
                continue

            if occurrences > 1:
                results.append(
                    OperationResult(
                        operation_index=idx,
                        path=str(file_path),
                        success=False,
                        message="Multiple matches",
                        error=f"old_string occurs {occurrences} times. Provide more context for unique match.",
                    )
                )
                fail_count += 1
                has_error = True
                continue

            # Apply the replacement
            new_content = current_content.replace(edit.old_string, edit.new_string, 1)

            if new_content == current_content:
                results.append(
                    OperationResult(
                        operation_index=idx,
                        path=str(file_path),
                        success=False,
                        message="No change",
                        error="Replacement resulted in no change",
                    )
                )
                fail_count += 1
                has_error = True
                continue

            current_content = new_content
            desc = f": {edit.description}" if edit.description else ""
            results.append(
                OperationResult(
                    operation_index=idx,
                    path=str(file_path),
                    success=True,
                    message=f"Applied edit{desc}",
                )
            )
            success_count += 1

        # Write the final content if any edits succeeded
        if success_count > 0 and current_content != old_content:
            try:
                ensure_parent_directory(file_path)
                file_path.write_text(current_content, encoding="utf-8")

                diff = FileDiff(
                    file_path=str(file_path),
                    old_content=old_content,
                    new_content=current_content,
                    is_new_file=is_new_file,
                )
            except (OSError, IOError) as e:
                # Mark all successful edits as failed due to write error
                for result in results:
                    if result.success:
                        result.success = False
                        result.error = f"Write failed: {e}"
                        success_count -= 1
                        fail_count += 1
                        has_error = True
                diff = None
        else:
            diff = None

        return {
            "operation_results": results,
            "diff": diff,
            "success_count": success_count,
            "fail_count": fail_count,
            "has_error": has_error,
        }

    def _build_no_match_error(self, old_string: str, content: str) -> str:
        """Build helpful error message when old_string not found."""
        lines = content.splitlines()
        search_terms = old_string.strip().split()[:3]

        if not search_terms:
            return "old_string not found (empty or whitespace only)"

        first_term = search_terms[0]
        partial_matches = []

        for i, line in enumerate(lines, 1):
            if first_term in line:
                partial_matches.append(f"Line {i}: {line.strip()[:60]}")
                if len(partial_matches) >= 2:
                    break

        if partial_matches:
            matches_str = "\n    ".join(partial_matches)
            return f"old_string not found. Partial matches:\n    {matches_str}\n  Check whitespace/indentation."
        else:
            return "old_string not found in file. Verify exact text including whitespace."

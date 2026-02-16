"""Professional shell command execution tool.

Features:
- Cross-platform support (Windows, Linux, macOS)
- Configurable security policies (blocked/confirm patterns)
- Environment variable filtering (protect secrets)
- Proper process group management for clean kills
- Graceful timeout handling (SIGTERM → SIGKILL)
- Output truncation (bytes and lines)
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from utils.runtime_logging import audit_event

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult

if TYPE_CHECKING:
    from config.config import Config

logger = logging.getLogger(__name__)


class ShellParams(BaseModel):
    """Parameters for shell command execution."""

    command: str = Field(
        ...,
        description="The shell command to execute",
    )
    timeout: int = Field(
        default=120,
        ge=1,
        le=600,
        description="Timeout in seconds (default: 120, max: 600)",
    )
    cwd: str | None = Field(
        default=None,
        description="Working directory for the command (default: project root)",
    )


class ShellTool(Tool):
    """Execute shell commands with security and resource controls."""

    name = "shell"
    kind = ToolKind.SHELL
    description = (
        "Execute a shell command. Use this for running system commands, "
        "scripts, builds, tests, and CLI tools. Commands are executed in "
        "bash (Unix) or cmd (Windows)."
    )
    schema = ShellParams

    def __init__(self, config: Config | None = None) -> None:
        super().__init__(config)
        self._shell_config = config.shell if config else None

    @property
    def _security(self):
        """Get security policy from config."""
        if self._shell_config:
            return self._shell_config.security
        # Fallback defaults
        from config.config import ShellSecurityPolicy

        return ShellSecurityPolicy()

    @property
    def _environment(self):
        """Get environment policy from config."""
        if self._shell_config:
            return self._shell_config.environment
        from config.config import ShellEnvironmentPolicy

        return ShellEnvironmentPolicy()

    @property
    def _max_output_bytes(self) -> int:
        if self._shell_config:
            return self._shell_config.max_output_bytes
        return 100 * 1024  # 100KB default

    @property
    def _max_output_lines(self) -> int:
        if self._shell_config:
            return self._shell_config.max_output_lines
        return 1000

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        """Execute the shell command."""
        params = ShellParams(**invocation.params)
        command = params.command.strip()

        if not command:
            return ToolResult.error_result("Command cannot be empty")

        # Check for blocked commands
        is_blocked, blocked_pattern = self._is_blocked(command)
        if is_blocked:
            logger.warning(f"Blocked command attempt: {command}")
            audit_event(
                "shell.blocked_command",
                "Shell command blocked by safety policy",
                command=command,
                blocked_pattern=blocked_pattern or "",
            )
            return ToolResult.error_result(
                f"Command blocked for safety: matches pattern '{blocked_pattern}'",
                metadata={"blocked_pattern": blocked_pattern},
            )

        # Validate timeout
        max_timeout = self._security.max_timeout
        timeout = min(params.timeout, max_timeout)

        # Resolve working directory
        working_dir = self._resolve_working_dir(invocation.cwd, params.cwd)
        if isinstance(working_dir, ToolResult):
            return working_dir  # Error result

        # Build filtered environment
        env = self._build_environment()

        # Create and run process
        try:
            process = await self._create_process(command, working_dir, env)
        except Exception as e:
            logger.error(f"Failed to create process: {e}")
            return ToolResult.error_result(f"Failed to start command: {e}")

        # Wait for completion with timeout
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._kill_process(process)
            return ToolResult.error_result(
                f"Command timed out after {timeout} seconds",
                metadata={
                    "command": command,
                    "timeout": timeout,
                    "working_directory": str(working_dir),
                },
            )

        # Decode output
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = process.returncode or 0

        # Format output
        output, truncated = self._format_output(stdout, stderr, exit_code)

        # Build result
        success = exit_code == 0

        return ToolResult(
            success=success,
            output=output,
            error=stderr.strip() if not success and stderr.strip() else None,
            truncated=truncated,
            metadata={
                "command": command,
                "exit_code": exit_code,
                "working_directory": str(working_dir),
            },
        )

    def _is_blocked(self, command: str) -> tuple[bool, str | None]:
        """Check if command matches any blocked pattern."""
        # Normalize whitespace for matching
        normalized = " ".join(command.split())

        for pattern in self._security.blocked_patterns:
            # Check exact match
            if fnmatch.fnmatch(normalized, pattern):
                return True, pattern
            # Check case-insensitive for certain dangerous patterns
            if fnmatch.fnmatch(normalized.lower(), pattern.lower()):
                return True, pattern
            # Check if pattern is contained in command
            if pattern in normalized:
                return True, pattern

        return False, None

    def _requires_confirmation(self, command: str) -> tuple[bool, str | None]:
        """Check if command requires user confirmation."""
        normalized = " ".join(command.split())

        for pattern in self._security.confirm_patterns:
            if fnmatch.fnmatch(normalized, pattern):
                return True, pattern
            if fnmatch.fnmatch(normalized.lower(), pattern.lower()):
                return True, pattern

        return False, None

    def _resolve_working_dir(self, invocation_cwd: Path, param_cwd: str | None) -> Path | ToolResult:
        """Resolve and validate working directory."""
        if param_cwd:
            working_dir = Path(param_cwd)
            if not working_dir.is_absolute():
                working_dir = invocation_cwd / working_dir
        else:
            working_dir = invocation_cwd

        working_dir = working_dir.resolve()

        if not working_dir.exists():
            return ToolResult.error_result(f"Working directory does not exist: {working_dir}")
        if not working_dir.is_dir():
            return ToolResult.error_result(f"Path is not a directory: {working_dir}")

        return working_dir

    def _build_environment(self) -> dict[str, str]:
        """Build filtered environment for shell execution."""
        env = os.environ.copy()
        policy = self._environment

        if not policy.ignore_default_excludes:
            # Remove vars matching exclude patterns
            to_remove = []
            for var in env:
                for pattern in policy.exclude_patterns:
                    if fnmatch.fnmatch(var, pattern):
                        to_remove.append(var)
                        break

            for var in to_remove:
                del env[var]
                logger.debug(f"Excluded env var: {var}")

        # Add explicit vars
        if policy.set_vars:
            env.update(policy.set_vars)

        return env

    async def _create_process(
        self,
        command: str,
        cwd: Path,
        env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Create subprocess with proper platform handling."""
        if sys.platform == "win32":
            # Windows: use shell directly
            return await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
        else:
            # Unix: use bash with new session for process group
            return await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                command,
                cwd=str(cwd),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,  # Create new process group
            )

    async def _kill_process(self, process: asyncio.subprocess.Process) -> None:
        """Gracefully kill process and its children."""
        if process.returncode is not None:
            return  # Already terminated

        if sys.platform == "win32":
            # Windows: just kill
            try:
                process.kill()
            except ProcessLookupError:
                pass
        else:
            # Unix: graceful shutdown with process group
            try:
                pgid = os.getpgid(process.pid)

                # Send SIGTERM first
                os.killpg(pgid, signal.SIGTERM)

                # Wait up to 5 seconds for graceful shutdown
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    # Force kill
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            except ProcessLookupError:
                pass  # Already dead
            except OSError as e:
                logger.warning(f"Error killing process: {e}")
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

        # Ensure process is reaped
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    def _format_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> tuple[str, bool]:
        """Format command output with truncation."""
        truncated = False
        parts = []

        # Add stdout
        if stdout.strip():
            parts.append(stdout.rstrip())

        # Add stderr (marked if present)
        if stderr.strip():
            if parts:
                parts.append("")  # Blank line separator
            parts.append(f"[stderr]\n{stderr.rstrip()}")

        parts.append(f"\n(exit code: {exit_code})")

        output = "\n".join(parts)

        # Truncate by bytes
        if len(output.encode("utf-8")) > self._max_output_bytes:
            # Find a safe truncation point
            output = output[: self._max_output_bytes]
            # Try to break at a newline
            last_newline = output.rfind("\n", -200)
            if last_newline > len(output) - 500:
                output = output[:last_newline]
            output += "\n\n...[output truncated]"
            truncated = True

        # Truncate by lines
        lines = output.splitlines()
        if len(lines) > self._max_output_lines:
            output = "\n".join(lines[: self._max_output_lines])
            output += f"\n\n...[truncated, showing {self._max_output_lines} of {len(lines)} lines]"
            truncated = True

        return output.strip(), truncated

    async def get_confirmation(
        self,
        invocation: ToolInvocation,
    ) -> ToolConfirmation | None:
        """Check if command requires confirmation."""
        params = ShellParams(**invocation.params)
        command = params.command.strip()

        # Always provide confirmation info for mutating shell commands
        needs_confirm, pattern = self._requires_confirmation(command)
        description = (
            f"Command matches dangerous pattern '{pattern}'. Confirm execution?"
            if needs_confirm
            else f"Execute shell command in {invocation.cwd}"
        )

        from safety.approval import is_dangerous_command

        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            params=invocation.params,
            description=description,
            command=command,
            is_dangerous=is_dangerous_command(command),
        )

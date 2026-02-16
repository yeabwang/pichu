# ruff: noqa: E402

import asyncio
import logging
import os
import sys
import warnings
from pathlib import Path

import click

_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_ROOT = _PROJECT_ROOT / "src"
if _SRC_ROOT.exists():
    src_path = str(_SRC_ROOT)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

from agent import Agent, AgentEventType, Session
from commands import create_command_router
from config import Config, get_system_config_path, load_config
from safety.approval import ApprovalResponse
from tools.base import ToolConfirmation
from ui.tui import TUI, get_console
from utils.runtime_logging import configure_runtime_logging
from utils.session_storage import (
    SessionReferenceAmbiguousError,
    SessionReferenceError,
    SessionReferenceNotFoundError,
    SessionStorage,
)

BANNER = """
⢰⣶⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀     ⢀⢀
⠀⣿⣿⣿⣷⣤⡀⠀⠀⠀⠀⠀⠀⠀   ⠀⠀⣀⣤⣶⣾⣿
⠀⠘⢿⣿⣿⣿⣿⣦⣀⣀⣀⣄⣀⣀⣠⣀⣤⣶⣿⣿⣿⣿⣿⠇   ▄▀▀▀▄ ▀  ▄▀▀▀  █  █  █  █
⠀⠀⠈⠻⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠋⠀   █▀▀▀  █  █     █▀▀█  █  █
⠀⠀⠀⠀⣰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⠋⠀⠀⠀    ▀    ▀▀  ▀▀▀▀  ▀  ▀   ▀▀▀
⠀⠀⠀⢠⣿⣿⡏⠆⢹⣿⣿⣿⣿⣿⣿⠒⠈⣿⣿⣿⣇⠀⠀⠀
⠀⠀⠀⣼⣿⣿⣷⣶⣿⣿⣛⣻⣿⣿⣿⣶⣾⣿⣿⣿⣿⡀⠀⠀    ░▒▓ code, compile, conquer. ▓▒░
⠀⠀⠀⡁⠀⠈⣿⣿⣿⣿⢟⣛⡻⣿⣿⣿⣟⠀⠀⠈⣿⡇⠀⠀
⠀⠀⠀⢿⣶⣿⣿⣿⣿⣿⡻⣿⡿⣿⣿⣿⣿⣶⣶⣾⣿⣿⠀⠀
⠀⠀⠘⣿⣿⣿⣿⣿⣿⣿⣿⣷⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡆⠀
⠀⠀⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡆⠀
""".strip()

console = get_console()


class CLI:
    def __init__(self, config: "Config"):
        self._config = config
        self.agent: Agent | None = None
        self.tui = TUI(console, self._config)

        # Slash command system
        self._router, self._registry = create_command_router(
            project_root=Path(config.cwd) if hasattr(config, "cwd") else None,
        )

        # Session resume state
        self._resume_session_id: str | None = None
        self._continue_session: bool = False

    def set_resume(self, session_id: str | None = None, continue_latest: bool = False) -> None:
        """Configure session resumption before run_interactive()."""
        self._resume_session_id = session_id
        self._continue_session = continue_latest

    async def _approval_callback(self, confirmation: ToolConfirmation) -> ApprovalResponse:
        """Async wrapper around the TUI approval prompt.

        Uses run_in_executor so the blocking keyboard input doesn't
        starve the event loop or get swallowed on Windows.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.tui.prompt_tool_approval, confirmation)

    async def run_single(self, message: str) -> str | None:
        async with Agent(self._config) as agent:
            self.agent = agent
            # Wire the approval callback
            if agent.session is None:
                raise RuntimeError("Agent session not initialized")
            agent.session.approval_manager.set_confirmation_callback(self._approval_callback)
            return await self._process_message(message)

    async def run_interactive(self) -> None:
        model_configured = self._is_model_configured()
        model_display = self._config.model if model_configured else "Not set (use /login to set your model)"

        missing_bootstrap_files = self._get_missing_bootstrap_files()
        tips = "/help for commands • /exit to quit"
        if missing_bootstrap_files:
            tips = f"{tips} • /init to generate project files"

        self.tui.print_welcome(
            banner=BANNER,
            info={
                "Model": model_display,
                "CWD": str(self._config.cwd),
            },
            tips=tips,
        )
        if missing_bootstrap_files:
            missing = ", ".join(missing_bootstrap_files)
            console.print(
                f"[warning]Project setup incomplete:[/warning] missing {missing}. Run [command]/init[/command]."
            )

        if not model_configured:
            console.print(
                "[warning]Model not set:[/warning] use [command]/login[/command] to configure provider and model."
            )

        # --- Resolve session to resume ---
        resume_id = self._resolve_resume_session()

        if resume_id:
            agent = await self._resume_agent(resume_id)
            if not agent:
                console.print("[yellow]Could not resume session. Starting new.[/yellow]")
                agent = Agent(self._config)
        else:
            agent = Agent(self._config)

        async with agent:
            self.agent = agent
            # Wire the approval callback
            if agent.session is None:
                raise RuntimeError("Agent session not initialized")
            agent.session.approval_manager.set_confirmation_callback(self._approval_callback)

            # If resumed, replay transcript
            _resume_entries = getattr(agent, "_resume_entries", None)
            if resume_id and _resume_entries:
                agent.session.replay_transcript(_resume_entries)
                title = agent.session.get_title()
                console.print(f"[dim]Resumed session: {title or resume_id[:8]}[/dim]")
                del agent._resume_entries  # type: ignore[attr-defined]

            while True:
                try:
                    user_input = self.tui.get_user_input()
                    if not user_input:
                        continue

                    # Slash command handling
                    if self._router.is_command(user_input):
                        if agent.session is None:
                            raise RuntimeError("Agent session not initialized")
                        result = await self._router.dispatch(
                            user_input,
                            session=agent.session,
                            tui=self.tui,
                            config=self._config,
                        )
                        if result.output:
                            console.print(result.output)
                        if result.error:
                            console.print(f"[error]{result.error}[/error]")
                        if result.should_exit:
                            self.tui.print_goodbye("Session ended. Happy coding!")
                            break
                        if result.inject_prompt:
                            await self._process_message(result.inject_prompt)
                        continue

                    # Auto-title on first user message
                    if agent.session is None:
                        raise RuntimeError("Agent session not initialized")
                    if agent.session.turn_count == 0 and agent.session.storage:
                        title = user_input[:60].strip()
                        agent.session.set_title(title)

                    await self._process_message(user_input)
                except (KeyboardInterrupt, EOFError):
                    self.tui.print_goodbye("Session ended. Happy coding!")
                    break

    def _get_missing_bootstrap_files(self) -> list[str]:
        project_dir_name = os.environ.get("PICHU_PROJECT_DIR", ".pichu")
        config_file_name = os.environ.get("PICHU_CONFIG_FILE", "config.toml")
        project_root = Path(self._config.cwd)

        expected_files = [
            project_root / "AGENTS.md",
            project_root / project_dir_name / config_file_name,
        ]
        return [str(path.relative_to(project_root)) for path in expected_files if not path.exists()]

    def _is_model_configured(self) -> bool:
        if os.environ.get("LLM_MODEL", "").strip():
            return True

        project_config_path = self._find_project_config_path()
        if project_config_path and self._config_has_llm_model(project_config_path):
            return True

        return self._config_has_llm_model(get_system_config_path())

    def _find_project_config_path(self) -> Path | None:
        project_dir_name = os.environ.get("PICHU_PROJECT_DIR", ".pichu")
        config_file_name = os.environ.get("PICHU_CONFIG_FILE", "config.toml")
        current = Path(self._config.cwd).resolve()

        while True:
            candidate = current / project_dir_name / config_file_name
            if candidate.exists() and candidate.is_file():
                return candidate
            if current.parent == current:
                break
            current = current.parent

        return None

    @staticmethod
    def _config_has_llm_model(config_path: Path) -> bool:
        if not config_path.exists() or not config_path.is_file():
            return False

        try:
            from tomlkit import parse as toml_parse

            doc = toml_parse(config_path.read_text(encoding="utf-8"))
            llm = doc.get("llm")
            if llm is None:
                return False
            model = llm.get("model")
            return bool(str(model).strip()) if model is not None else False
        except Exception:
            return False

    def _resolve_resume_session(self) -> str | None:
        """Determine which session to resume, if any."""
        session_cfg = getattr(self._config, "session", None)
        if not session_cfg or not session_cfg.enabled:
            return None

        cwd = str(self._config.cwd) if hasattr(self._config, "cwd") else ""
        storage = SessionStorage(
            storage_dir=Path(session_cfg.storage_dir).expanduser(),
            cleanup_days=session_cfg.cleanup_days,
            max_sessions=session_cfg.max_sessions,
        )

        if self._resume_session_id:
            try:
                meta = storage.resolve_session_reference(self._resume_session_id, cwd=cwd)
                return meta.session_id
            except SessionReferenceNotFoundError:
                # Allow explicit IDs/titles from outside cwd when user requested --resume.
                try:
                    meta = storage.resolve_session_reference(self._resume_session_id)
                    return meta.session_id
                except SessionReferenceError as exc:
                    self._print_session_resolution_error(exc)
                    return None
            except SessionReferenceError as exc:
                self._print_session_resolution_error(exc)
                return None

        if self._continue_session:
            latest_meta = storage.find_most_recent(cwd=cwd)
            if latest_meta:
                return latest_meta.session_id
            console.print("[yellow]No previous session found to continue.[/yellow]")
            return None

        return None

    def _print_session_resolution_error(self, exc: SessionReferenceError) -> None:
        if isinstance(exc, SessionReferenceAmbiguousError):
            sample = ", ".join(meta.session_id[:8] for meta in exc.matches[:5])
            suffix = "..." if len(exc.matches) > 5 else ""
            console.print(f"[yellow]{exc}[/yellow]")
            console.print(f"[dim]Matching sessions: {sample}{suffix}[/dim]")
            return
        console.print(f"[yellow]{exc}[/yellow]")

    async def _resume_agent(self, session_id: str) -> Agent | None:
        """Create an Agent configured to resume a previous session."""
        result = Session.from_transcript(self._config, session_id)
        if not result:
            return None

        session, entries = result
        agent = Agent(self._config)
        agent.session = session
        # Store entries for replay after initialize()
        agent._resume_entries = entries  # type: ignore[attr-defined]
        return agent

    def _get_tool_kind(self, tool_name: str) -> str | None:
        if not self.agent or not self.agent.session:
            return None
        tool = self.agent.session.tool_registry.get(tool_name)
        return tool.kind.value if tool else None

    async def _process_message(self, message: str) -> str | None:
        if not self.agent:
            raise ValueError("Agent not initialized.")

        agent_streaming = False
        full_response: str | None = ""
        async for event in self.agent.run(message):
            if event.type == AgentEventType.TEXT_DELTA:
                content = event.data.get("content", "")
                if not agent_streaming:
                    self.tui.begin_agent_response()
                    agent_streaming = True
                full_response += content
                self.tui.stream_agent_delta(content)

            elif event.type == AgentEventType.TEXT_COMPLETE:
                full_response = event.data.get("content", full_response)
                if agent_streaming:
                    self.tui.end_agent_response()
                    agent_streaming = False

            elif event.type == AgentEventType.AGENT_ERROR:
                error_message = event.data.get("error", "Unknown error")
                self.tui.stop_thinking()
                if agent_streaming:
                    self.tui.end_agent_response()
                    agent_streaming = False
                console.print(f"\n[error]Error: {error_message}[/error]")
                full_response = None

            elif event.type == AgentEventType.TOOL_CALL_START:
                tool_name = event.data.get("name", "unknown tool")
                tool_kind = self._get_tool_kind(tool_name) or "unknown"

                self.tui.tool_call_start(
                    event.data.get("call_id", ""),
                    tool_name,
                    tool_kind,
                    event.data.get("arguments", {}),
                )

            elif event.type == AgentEventType.TOOL_CALL_COMPLETE:
                tool_name = event.data.get("name", "unknown")
                tool_kind = self._get_tool_kind(tool_name) or "unknown"
                self.tui.tool_call_complete(
                    call_id=event.data.get("call_id", ""),
                    name=tool_name,
                    tool_kind=tool_kind,
                    output=event.data.get("output", ""),
                    success=event.data.get("success", False),
                    error=event.data.get("error"),
                    diff=event.data.get("diff"),
                    metadata=event.data.get("metadata"),
                    truncated=event.data.get("truncated", False),
                    exit_code=event.data.get("exit_code"),
                )

            elif event.type == AgentEventType.CONTEXT_COMPACTED:
                self.tui.print_context_event("compacted", event.data)

            elif event.type == AgentEventType.CONTEXT_PRUNED:
                self.tui.print_context_event("pruned", event.data)

            elif event.type == AgentEventType.CONTEXT_STATS:
                self.tui.update_context_tracker(event.data)

            elif event.type == AgentEventType.THINKING_START:
                self.tui.start_thinking()

            elif event.type == AgentEventType.THINKING_END:
                self.tui.stop_thinking()

            elif event.type == AgentEventType.LLM_RETRY:
                self.tui.show_llm_retry(
                    event.data.get("attempt", 0),
                    event.data.get("max_attempts", 0),
                    event.data.get("error", "unknown"),
                    event.data.get("wait_seconds", 0),
                )

            elif event.type == AgentEventType.EMPTY_RESPONSE:
                self.tui.show_empty_response()

            elif event.type == AgentEventType.LOOP_DETECTED:
                self.tui.show_loop_warning(
                    event.data.get("strategy", "unknown"),
                    event.data.get("description", ""),
                    event.data.get("turn_count", 0),
                    event.data.get("action_taken", "warn_and_nudge"),
                )

            elif event.type == AgentEventType.APPROVAL_DENIED:
                self.tui.print_approval_denied(
                    event.data.get("tool_name", "unknown"),
                    event.data.get("reason", "denied"),
                )
        return full_response


def _configure_logging(config: Config) -> None:
    """Configure runtime logging for interactive runs."""
    configure_runtime_logging(config)
    if config.debug:
        logging.getLogger(__name__).debug("Debug logging enabled")
        console.print("[dim]Debug mode enabled[/dim]")


@click.command()
@click.argument("prompt", required=False)
@click.option(
    "--cwd",
    "-c",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help="Working directory for the agent.",
)
@click.option(
    "--continue",
    "-C",
    "continue_session",
    is_flag=True,
    default=False,
    help="Resume the most recent session in this directory.",
)
@click.option(
    "--resume",
    "-r",
    "resume_id",
    type=str,
    default=None,
    help="Resume a specific session by ID or title.",
)
def main(
    prompt: str | None = None,
    cwd: Path | None = None,
    continue_session: bool = False,
    resume_id: str | None = None,
):
    try:
        config = load_config(cwd=cwd)
    except Exception as e:
        console.print(f"[red]Error loading config:[/red] {e}")
        sys.exit(1)

    _configure_logging(config)

    errors = config.validate()
    if errors:
        for error in errors:
            console.print(f"[red]Error:[/red] {error}")
        sys.exit(1)

    cli = CLI(config)

    # Configure session resumption
    if continue_session or resume_id:
        cli.set_resume(session_id=resume_id, continue_latest=continue_session)

    # Suppress Windows asyncio transport warnings during shutdown
    if sys.platform == "win32":
        warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")

    if prompt:
        result = asyncio.run(cli.run_single(prompt))
        if result is None:
            sys.exit(1)
    else:
        asyncio.run(cli.run_interactive())

    # Force garbage collection to clean up transports before exit
    if sys.platform == "win32":
        import gc

        gc.collect()


if __name__ == "__main__":
    main()

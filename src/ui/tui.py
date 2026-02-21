import asyncio
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Console

from ui.capabilities.detection import is_color_supported, is_dumb_terminal, is_interactive_tty
from ui.controller.event_router import TUIEventRouterMixin
from ui.output.renderer import DifferentialRichRenderer, RenderState
from ui.theme import AGENT_THEME

if TYPE_CHECKING:
    from commands.base import CommandSpec
    from config import Config

_console: Console | None = None


def get_console() -> Console:
    global _console
    if _console is None:
        interactive_tty = is_interactive_tty()
        dumb_terminal = is_dumb_terminal()
        color_supported = is_color_supported()
        _console = Console(
            theme=AGENT_THEME,
            highlight=False,
            force_terminal=interactive_tty and not dumb_terminal,
            no_color=dumb_terminal or not color_supported,
        )
    return _console


class TUI(TUIEventRouterMixin):
    def __init__(
        self,
        console: Console | None = None,
        config: "Config | None" = None,
    ):
        self.console = console if console else get_console()
        self._config = config
        self._interactive_tty = is_interactive_tty()
        self._dumb_terminal = is_dumb_terminal()
        self._agent_stream_open = False
        self._tool_args_by_call_id: dict[str, dict[str, Any]] = {}
        self._thinking_live = None
        self._latest_context_stats: dict[str, Any] | None = None
        self._render_state = RenderState()
        self._renderer = DifferentialRichRenderer(self.console, self._render_state)
        if not self._interactive_tty:
            self._renderer.set_footer_live_enabled(False)
        self._interrupt_handler: Callable[[], None] | None = None
        self._input_manager: Any | None = None
        self._init_input_manager()

    def _init_input_manager(self) -> None:
        if not self._interactive_tty:
            return
        try:
            from config.loader import get_config_dir
            from ui.input.manager import InputManager

            history_path = get_config_dir() / "prompt-history.txt"
            history_path.parent.mkdir(parents=True, exist_ok=True)
            self._input_manager = InputManager(
                history_path=history_path,
                footer_provider=self._input_footer_text,
                cwd=self.cwd,
            )
            self._input_manager.set_interrupt_handler(self._interrupt_handler)
            self._renderer.set_footer_live_enabled(False)
        except (ImportError, OSError):
            self._input_manager = None

    def _input_footer_text(self) -> str:
        return self._renderer.footer_text()

    def set_command_specs(self, specs: list["CommandSpec"]) -> None:
        if not self._input_manager:
            return
        from ui.input.manager import CommandChoice

        self._input_manager.set_commands(
            [
                CommandChoice(
                    name=spec.name,
                    description=spec.description,
                    aliases=spec.aliases,
                )
                for spec in specs
            ]
        )

    def set_interrupt_handler(self, interrupt_handler: Callable[[], None] | None) -> None:
        self._interrupt_handler = interrupt_handler
        if self._input_manager:
            self._input_manager.set_interrupt_handler(interrupt_handler)

    def set_agent_running(self, is_agent_running: bool) -> None:
        if self._input_manager:
            self._input_manager.set_agent_running(is_agent_running)

    async def get_user_input(self, prompt: str = "❯") -> str:
        self.flush_deferred_ui(force=True)
        self._renderer.begin_prompt_block()
        if self._input_manager:
            return await self._input_manager.prompt_async(prompt)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            input,
            f"{prompt} ",
        )

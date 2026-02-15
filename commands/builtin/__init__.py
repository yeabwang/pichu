"""Builtin slash commands registration."""

from commands.base import CommandRegistry, SlashCommand
from commands.builtin.agents import AgentsCommand
from commands.builtin.clear import ClearCommand
from commands.builtin.compact import CompactCommand
from commands.builtin.config_cmd import ConfigCommand
from commands.builtin.context import ContextCommand
from commands.builtin.copy_cmd import CopyCommand
from commands.builtin.cost import CostCommand
from commands.builtin.debug import DebugCommand
from commands.builtin.doctor import DoctorCommand
from commands.builtin.exit import ExitCommand
from commands.builtin.export import ExportCommand
from commands.builtin.fork import ForkCommand
from commands.builtin.github import GithubCommand
from commands.builtin.help import HelpCommand
from commands.builtin.hooks import HooksCommand
from commands.builtin.init import InitCommand
from commands.builtin.login import LoginCommand
from commands.builtin.mcp import McpCommand
from commands.builtin.memory import MemoryCommand
from commands.builtin.model import ModelCommand
from commands.builtin.permissions import PermissionsCommand
from commands.builtin.rename import RenameCommand
from commands.builtin.rewind import RewindCommand
from commands.builtin.sessions import SessionsCommand
from commands.builtin.stats import StatsCommand
from commands.builtin.status import StatusCommand
from commands.builtin.tasks import TasksCommand
from commands.builtin.theme import ThemeCommand

BUILTIN_COMMANDS: tuple[type[SlashCommand], ...] = (
    HelpCommand,
    ExitCommand,
    ClearCommand,
    CompactCommand,
    CostCommand,
    ContextCommand,
    StatsCommand,
    StatusCommand,
    ModelCommand,
    ThemeCommand,
    CopyCommand,
    ExportCommand,
    ConfigCommand,
    MemoryCommand,
    InitCommand,
    McpCommand,
    GithubCommand,
    DoctorCommand,
    DebugCommand,
    PermissionsCommand,
    AgentsCommand,
    TasksCommand,
    HooksCommand,
    SessionsCommand,
    RewindCommand,
    ForkCommand,
    RenameCommand,
    LoginCommand,
)


def build_builtin_commands() -> list[SlashCommand]:
    """Create builtin command instances in their registration order."""
    return [command_type() for command_type in BUILTIN_COMMANDS]


def register_all_commands(registry: CommandRegistry) -> None:
    """Register all builtin slash commands."""
    for command in build_builtin_commands():
        registry.register(command)

"""Initialize project with AGENTS.md."""

from __future__ import annotations

from typing import TYPE_CHECKING

from commands.base import CommandResult, SlashCommand

if TYPE_CHECKING:
    from agent.session import Session
    from config import Config
    from ui.tui import TUI


_AGENTS_TEMPLATE = """# {project_name}

## Architecture
- Main runtime modules and boundaries:
-

## Tech Stack
- Languages, frameworks, and critical infrastructure:
-

## Patterns
- Reusable implementation patterns contributors should follow:
-

## Conventions
- Naming, formatting, and review expectations:
-

## Gotchas
- Known pitfalls, environment constraints, or unsafe operations:
-

## Testing
- Commands to run and quality gates before merge:
-
"""


class InitCommand(SlashCommand):
    name = "init"
    description = "Initialize project with AGENTS.md"
    usage = "/init"
    aliases = []

    async def execute(self, args: str, session: "Session", tui: "TUI", config: "Config") -> CommandResult:
        import os

        project_root = config.cwd
        agents_file = project_root / "AGENTS.md"
        local_file = project_root / "AGENTS.local.md"
        project_dir_name = os.environ.get("PICHU_PROJECT_DIR", ".PICHU")
        config_file_name = os.environ.get("PICHU_CONFIG_FILE", "config.toml")
        pichu_dir = project_root / project_dir_name

        created = []

        # Create AGENTS.md if not exists
        if agents_file.exists():
            tui.console.print(f"  [dim]AGENTS.md already exists at {agents_file}[/dim]")
        else:
            project_name = project_root.name
            content = _AGENTS_TEMPLATE.format(project_name=project_name)
            agents_file.write_text(content, encoding="utf-8")
            created.append("AGENTS.md")

        # Create AGENTS.local.md stub
        if not local_file.exists():
            local_file.write_text(
                "# Local Instructions (gitignored)\n\n## Personal Preferences\n\n## Local Environment\n\n",
                encoding="utf-8",
            )
            created.append("AGENTS.local.md")

        # Create project config directory
        if not pichu_dir.exists():
            pichu_dir.mkdir(parents=True, exist_ok=True)
            created.append(f"{project_dir_name}/")

        # Create project config file stub
        config_file = pichu_dir / config_file_name
        if not config_file.exists():
            config_file.write_text(
                "# Pichu project configuration\n"
                "# See documentation for available settings\n\n"
                "[llm]\n"
                '# model = "your-model-here"\n\n'
                "[shell]\n"
                "# default_timeout = 30\n\n",
                encoding="utf-8",
            )
            created.append(f"{project_dir_name}/{config_file_name}")

        # Suggest adding AGENTS.local.md to .gitignore
        gitignore = project_root / ".gitignore"
        gitignore_updated = False
        if gitignore.exists():
            content = gitignore.read_text(encoding="utf-8")
            if "AGENTS.local.md" not in content:
                with open(gitignore, "a", encoding="utf-8") as f:
                    f.write("\n# Pichu local config\nAGENTS.local.md\n")
                gitignore_updated = True

        tui.console.print()
        if created:
            for item in created:
                tui.console.print(f"  [success]✓[/success] Created {item}")
        else:
            tui.console.print("  [dim]Project already initialized — all files exist.[/dim]")

        if gitignore_updated:
            tui.console.print("  [success]✓[/success] Added AGENTS.local.md to .gitignore")

        tui.console.print()
        tui.console.print("  [dim]Edit AGENTS.md to teach Pichu about your project.[/dim]")
        tui.console.print("  [dim]Use AGENTS.local.md for personal preferences (gitignored).[/dim]")
        tui.console.print()

        return CommandResult()

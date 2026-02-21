from __future__ import annotations

from rich.theme import Theme

# One Dark Pro palette
PALETTE = {
    "bg0": "#282c34",
    "bg1": "#2f343f",
    "bg2": "#3e4451",
    "fg0": "#abb2bf",
    "fg1": "#d7dae0",
    "fg2": "#7f848e",
    "fg3": "#5c6370",
    "blue": "#61afef",
    "cyan": "#56b6c2",
    "green": "#98c379",
    "yellow": "#e5c07b",
    "orange": "#d19a66",
    "red": "#e06c75",
    "magenta": "#c678dd",
}

SYNTAX_HIGHLIGHT_THEME = "one-dark"


def _hex_to_ansi_fg(value: str) -> str:
    value = value.lstrip("#")
    return f"\x1b[38;2;{int(value[0:2], 16)};{int(value[2:4], 16)};{int(value[4:6], 16)}m"


PROMPT_CONTINUATION_ANSI = _hex_to_ansi_fg(PALETTE["fg2"])
PROMPT_FOOTER_ANSI = _hex_to_ansi_fg(PALETTE["fg2"])
PROMPT_SYMBOL_ANSI = _hex_to_ansi_fg(PALETTE["blue"])

AGENT_THEME_STYLES = {
    # Semantic UI hierarchy
    "primary": f"bold {PALETTE['blue']}",
    "secondary": PALETTE["magenta"],
    "accent": PALETTE["green"],
    "dim": f"dim {PALETTE['fg0']}",
    "muted": PALETTE["fg2"],
    "subtle": PALETTE["fg3"],
    "faint": PALETTE["bg2"],
    "highlight": f"bold {PALETTE['blue']}",
    # Transcript actors
    "agent": f"bold {PALETTE['blue']}",
    "agent.name": f"bold {PALETTE['blue']}",
    "user": f"bold {PALETTE['yellow']}",
    "user.name": f"bold {PALETTE['yellow']}",
    "system": f"dim {PALETTE['fg2']}",
    # Content/code
    "code": PALETTE["fg0"],
    "code.block": PALETTE["fg0"],
    "code.inline": PALETTE["green"],
    "path": f"underline {PALETTE['blue']}",
    "command": f"bold {PALETTE['yellow']}",
    # Status
    "success": f"bold {PALETTE['green']}",
    "error": f"bold {PALETTE['red']}",
    "warning": f"bold {PALETTE['yellow']}",
    "info": f"bold {PALETTE['cyan']}",
    "pending": f"dim {PALETTE['yellow']}",
    # Streaming/progress
    "thinking": f"dim italic {PALETTE['cyan']}",
    "streaming": PALETTE["fg0"],
    "spinner": PALETTE["cyan"],
    # Layout
    "border": PALETTE["bg2"],
    "border.focus": PALETTE["blue"],
    "border.highlight": PALETTE["cyan"],
    "border.muted": PALETTE["bg1"],
    "border.success": PALETTE["green"],
    "border.error": PALETTE["red"],
    "header": f"bold {PALETTE['fg1']}",
    "footer": PALETTE["fg2"],
    "prompt": f"bold {PALETTE['blue']}",
    "prompt.symbol": f"bold {PALETTE['blue']}",
    # Context/token usage
    "tokens": f"dim {PALETTE['fg3']}",
    "tokens.input": PALETTE["blue"],
    "tokens.output": PALETTE["magenta"],
    "tokens.cached": PALETTE["green"],
    "context.utilization.low": PALETTE["green"],
    "context.utilization.medium": PALETTE["yellow"],
    "context.utilization.high": PALETTE["red"],
    # Task tree
    "task.in_progress": f"bold {PALETTE['yellow']}",
    "task.available": f"bold {PALETTE['green']}",
    "task.blocked": f"bold {PALETTE['red']}",
    # Tool UI
    "tool": f"bold {PALETTE['yellow']}",
    "tool.name": f"bold {PALETTE['yellow']}",
    "tool.arg": PALETTE["fg2"],
    "tool.result": PALETTE["green"],
    "tool.read": PALETTE["blue"],
    "tool.write": PALETTE["magenta"],
    "tool.shell": PALETTE["yellow"],
    "tool.network": PALETTE["cyan"],
    "tool.memory": PALETTE["green"],
    "tool.mcp": PALETTE["magenta"],
    "tool.unknown": PALETTE["fg2"],
    # Diff/markdown
    "diff.add": PALETTE["green"],
    "diff.remove": PALETTE["red"],
    "diff.context": PALETTE["fg2"],
    "markdown.h1": f"bold {PALETTE['blue']} underline",
    "markdown.h2": f"bold {PALETTE['cyan']}",
    "markdown.h3": f"bold {PALETTE['fg1']}",
    "markdown.link": f"underline {PALETTE['blue']}",
    "markdown.bold": "bold",
    "markdown.italic": "italic",
    # Welcome
    "banner": f"bold {PALETTE['blue']}",
    "welcome.label": PALETTE["fg2"],
    "welcome.value": PALETTE["blue"],
    "welcome.tips": f"dim italic {PALETTE['fg2']}",
    "goodbye": PALETTE["fg2"],
    # Rich color aliases for legacy markup tags used in commands.
    "black": PALETTE["bg0"],
    "bright_black": PALETTE["fg3"],
    "red": PALETTE["red"],
    "bright_red": f"bold {PALETTE['red']}",
    "green": PALETTE["green"],
    "bright_green": f"bold {PALETTE['green']}",
    "yellow": PALETTE["yellow"],
    "bright_yellow": f"bold {PALETTE['yellow']}",
    "blue": PALETTE["blue"],
    "bright_blue": f"bold {PALETTE['blue']}",
    "cyan": PALETTE["cyan"],
    "bright_cyan": f"bold {PALETTE['cyan']}",
    "magenta": PALETTE["magenta"],
    "bright_magenta": f"bold {PALETTE['magenta']}",
    "white": PALETTE["fg0"],
    "bright_white": PALETTE["fg1"],
    "grey30": PALETTE["fg3"],
    "grey35": PALETTE["fg3"],
    "grey42": PALETTE["fg2"],
    "grey62": PALETTE["fg2"],
    "grey70": PALETTE["fg1"],
}

AGENT_THEME = Theme(AGENT_THEME_STYLES)

PROMPT_STYLE_RULES = {
    "completion-menu": f"bg:{PALETTE['bg0']} {PALETTE['fg2']}",
    "completion-menu.completion": f"bg:{PALETTE['bg0']} {PALETTE['blue']}",
    "completion-menu.meta.completion": f"bg:{PALETTE['bg0']} {PALETTE['fg3']}",
    "completion-menu.completion.current": f"bg:{PALETTE['blue']} {PALETTE['bg0']}",
    "completion-menu.meta.completion.current": f"bg:{PALETTE['bg2']} {PALETTE['fg1']}",
    # Keep fuzzy-highlight semantic emphasis without overriding foreground contrast.
    "fuzzymatch.inside.character": "bold underline",
    "scrollbar.background": f"bg:{PALETTE['bg0']}",
    "scrollbar.button": f"bg:{PALETTE['bg2']}",
}

THEME_PRESETS = {
    "one-dark": {
        "description": "One Dark Pro inspired (default)",
        "primary": f"bold {PALETTE['blue']}",
        "secondary": PALETTE["magenta"],
        "accent": PALETTE["green"],
        "agent": f"bold {PALETTE['blue']}",
        "border.focus": PALETTE["blue"],
        "banner": f"bold {PALETTE['blue']}",
        "prompt.symbol": f"bold {PALETTE['blue']}",
    },
    "dracula": {
        "description": "Dracula inspired modern dark palette",
        "primary": "bold #8be9fd",
        "secondary": "#ff79c6",
        "accent": "#50fa7b",
        "agent": "bold #8be9fd",
        "border.focus": "#bd93f9",
        "banner": "bold #8be9fd",
        "prompt.symbol": "bold #8be9fd",
    },
    "nord": {
        "description": "Nord inspired cool dark palette",
        "primary": "bold #81a1c1",
        "secondary": "#b48ead",
        "accent": "#a3be8c",
        "agent": "bold #88c0d0",
        "border.focus": "#81a1c1",
        "banner": "bold #88c0d0",
        "prompt.symbol": "bold #88c0d0",
    },
}

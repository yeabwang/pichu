from __future__ import annotations

from typing import TYPE_CHECKING

from tools.base import Tool
from tools.builtin.batch_apply import BatchApplyTool
from tools.builtin.edit_file import EditFileTool
from tools.builtin.glob import GlobTool
from tools.builtin.grep import GrepTool
from tools.builtin.list_dir import ListDirTool

# Memory tool
from tools.builtin.memory import MemoryTool
from tools.builtin.read_file import ReadFileTool
from tools.builtin.shell import ShellTool

# Sub-agent tools (dynamic, user-definable agents)
from tools.builtin.subagent import SubAgentTool, create_subagent_tools

# Task orchestration tools
from tools.builtin.task_create import TaskCreateTool
from tools.builtin.task_get import TaskGetTool
from tools.builtin.task_list import TaskListTool
from tools.builtin.task_update import TaskUpdateTool
from tools.builtin.web_fetch import WebFetchTool

# Use structured tools with citations, security, and budgets
from tools.builtin.web_search import WebSearchTool
from tools.builtin.write_file import WriteFileTool

if TYPE_CHECKING:
    from config import Config

# Import shared components for session-level coordination
from utils.web_security import WebSecurityConfig, WebSecurityManager
from utils.web_types import (
    Citation,
    FetchedDocument,
    FindResult,
    ToolBudget,
    WebSearchResult,
    WebToolError,
    WebToolErrorCode,
)

__all__ = [
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "BatchApplyTool",
    "ShellTool",
    "ListDirTool",
    "GrepTool",
    "GlobTool",
    "WebSearchTool",
    "WebFetchTool",
    # Task orchestration tools
    "TaskCreateTool",
    "TaskUpdateTool",
    "TaskGetTool",
    "TaskListTool",
    # Memory tool
    "MemoryTool",
    # Sub-agent tools
    "SubAgentTool",
    "create_subagent_tools",
    # Shared web tool components
    "ToolBudget",
    "WebSearchResult",
    "FetchedDocument",
    "FindResult",
    "Citation",
    "WebToolError",
    "WebToolErrorCode",
    "WebSecurityManager",
    "WebSecurityConfig",
]


def get_all_builtin_tools(config: Config | None = None) -> list[Tool]:
    """Get all builtin tools with shared security manager and budget.

    Web tools share the same security manager for URL provenance tracking
    and the same budget for session-level limits.
    """
    # Create shared security manager and budget for web tools
    security_manager = None
    budget = None

    if config and hasattr(config, "web_search"):
        search_cfg = config.web_search

        # Security manager
        if hasattr(search_cfg, "security"):
            sec_cfg = search_cfg.security
            security_manager = WebSecurityManager(
                WebSecurityConfig(
                    allowed_domains=sec_cfg.allowed_domains,
                    blocked_domains=sec_cfg.blocked_domains,
                    allow_ip_addresses=sec_cfg.allow_ip_addresses,
                    allow_localhost=sec_cfg.allow_localhost,
                    allow_private_networks=sec_cfg.allow_private_networks,
                    allowed_schemes=sec_cfg.allowed_schemes,
                    require_url_provenance=sec_cfg.require_url_provenance,
                )
            )

        # Budget
        if hasattr(search_cfg, "budget"):
            budget_cfg = search_cfg.budget
            budget = ToolBudget(
                max_searches=budget_cfg.max_searches,
                max_results_per_search=budget_cfg.max_results_per_search,
                max_fetches=budget_cfg.max_fetches,
                max_bytes_total=budget_cfg.max_bytes_total,
            )

    return [
        ReadFileTool(config),
        WriteFileTool(config),
        EditFileTool(config),
        BatchApplyTool(config),
        ShellTool(config),
        ListDirTool(config),
        GrepTool(config),
        GlobTool(config),
        WebSearchTool(config, security_manager=security_manager, budget=budget),
        WebFetchTool(config, security_manager=security_manager, budget=budget),
        # Task orchestration tools
        TaskCreateTool(),
        TaskUpdateTool(),
        TaskGetTool(),
        TaskListTool(),
        # Memory tool
        MemoryTool(),
    ]

"""Session runtime state and integration wiring for the agent loop."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from client.llm_client import LLMClient
from config import Config
from context.compaction import ChatCompactor
from context.context_manager import ContextManager
from hooks.engine import HookEngine
from safety.approval import ApprovalManager
from safety.sandbox import SandboxConfig, init_sandbox, reset_sandbox
from subagents.loader import SubAgentLoader
from tools.builtin import (
    MemoryTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    ToolBudget,
    WebFetchTool,
    WebSearchTool,
    WebSecurityConfig,
    WebSecurityManager,
    create_subagent_tools,
)
from tools.mcp.mcp_manager import MCPManager
from tools.registry import create_default_tool_registry
from utils.agents_loader import AgentsLoader
from utils.checkpoint_manager import CheckpointManager
from utils.memory_manager import MemoryManager
from utils.session_storage import SessionStorage, TranscriptEntry
from utils.task_manager import TaskManager

logger = logging.getLogger(__name__)


class Session:
    """Owns per-session state: tools, context, memory, hooks, and persistence."""

    def __init__(self, config: Config, session_id: str | None = None):
        """Construct a session and initialize non-async integrations."""
        self._config = config
        self.tool_registry = create_default_tool_registry(self._config)
        self.client = LLMClient(self._config.llm)
        self.context_manager: ContextManager | None = None
        self.session_id = session_id or str(uuid.uuid4())
        self.created_at = datetime.now()
        self.updated_at = datetime.now()

        self._turn_count: int = 0
        self._session_persisted = False

        self._storage: SessionStorage | None = None
        self._checkpoint_manager: CheckpointManager | None = None
        self._init_persistence()
        self._init_safety()

        self._security_manager: WebSecurityManager | None = None
        self._budget: ToolBudget | None = None
        self._init_web_tracking()

        self._task_manager: TaskManager | None = None
        self._init_task_management()

        self._memory_manager: MemoryManager | None = None
        self._agents_loader: AgentsLoader | None = None
        self._init_memory_management()

        self._subagent_loader: SubAgentLoader | None = None
        self._init_subagents()

        self._mcp_manager: MCPManager = MCPManager(self._config)

        self.chat_compactor = ChatCompactor(self.client, self._config)

        self.approval_manager = ApprovalManager(
            config=self._config.approval,
            cwd=self._config.cwd,
        )

        self.hook_engine = self._init_hooks()

    def _init_safety(self) -> None:
        """Initialize global filesystem sandbox for this session."""
        safety_cfg = getattr(self._config, "safety", None)
        sandbox_cfg = getattr(safety_cfg, "sandbox", None)

        if sandbox_cfg and not sandbox_cfg.enabled:
            reset_sandbox()
            return

        if sandbox_cfg:
            init_sandbox(
                cwd=self._config.cwd,
                config=SandboxConfig(
                    allowed_directories=sandbox_cfg.allowed_directories,
                    restrict_to_cwd=sandbox_cfg.restrict_to_cwd,
                    allow_temp_writes=sandbox_cfg.allow_temp_writes,
                    blocked_patterns=sandbox_cfg.blocked_patterns,
                    blocked_extensions=sandbox_cfg.blocked_extensions,
                    allow_executables=sandbox_cfg.allow_executables,
                    sensitive_read_patterns=sandbox_cfg.sensitive_read_patterns,
                    restrict_reads_to_allowed_dirs=sandbox_cfg.restrict_reads_to_allowed_dirs,
                ),
            )
            return

        init_sandbox(cwd=self._config.cwd)

    def _init_hooks(self) -> HookEngine:
        """Build the hook engine snapshot for this session."""
        hooks_config = getattr(self._config, "hooks", None)
        if hooks_config is None or hooks_config.disabled:
            return HookEngine(disabled=True)

        engine_dict = hooks_config.to_engine_dict()
        if not engine_dict:
            return HookEngine(disabled=True)

        engine = HookEngine(
            hooks_config=engine_dict,
            session_id=self.session_id,
            cwd=str(self._config.cwd),
            permission_mode=self._config.approval.policy.value,
        )

        logger.debug(
            f"Hook engine initialized: {engine.get_hook_count()} handler(s) "
            f"across {len(engine.get_registered_events())} event(s)"
        )
        return engine

    def _init_persistence(self) -> None:
        """Initialize storage backends; session files are created lazily on save."""
        session_cfg = getattr(self._config, "session", None)
        if not session_cfg or not session_cfg.enabled:
            return

        try:
            storage_dir = Path(session_cfg.storage_dir).expanduser()
            self._storage = SessionStorage(
                storage_dir=storage_dir,
                cleanup_days=session_cfg.cleanup_days,
                max_sessions=session_cfg.max_sessions,
            )
            self._session_persisted = False

            self._storage.cleanup_old_sessions()
            self._storage.enforce_max_sessions()

        except Exception as e:
            logger.warning(f"Failed to initialize session persistence: {e}")
            self._storage = None
            self._checkpoint_manager = None

    def _ensure_session_persisted(self) -> None:
        """Create persistent session files and wire checkpoint-aware edit tools."""
        if not self._storage or self._session_persisted:
            return

        try:
            session_cfg = getattr(self._config, "session", None)
            cwd = str(self._config.cwd) if hasattr(self._config, "cwd") else ""
            model = self._config.model if hasattr(self._config, "model") else ""

            session_dir = self._storage.create_session(
                session_id=self.session_id,
                cwd=cwd,
                model=model,
            )

            if session_cfg and session_cfg.checkpoints_enabled:
                self._checkpoint_manager = CheckpointManager(session_dir)

                from tools.builtin.edit_file import EditFileTool
                from tools.builtin.write_file import WriteFileTool

                for tool in self.tool_registry.get_tools():
                    if isinstance(tool, EditFileTool):
                        tool.set_checkpoint_manager(self._checkpoint_manager)
                    elif isinstance(tool, WriteFileTool):
                        tool.set_checkpoint_manager(self._checkpoint_manager)

            self._session_persisted = True

        except Exception as e:
            logger.warning(f"Failed to persist session: {e}")
            self._session_persisted = True

    def _refresh_context_manager(self) -> None:
        """Rebuild context with the current tool registry snapshot."""
        self.context_manager = ContextManager(
            self._config,
            tools=self.tool_registry.get_tools(),
        )

    async def initialize(self) -> None:
        """Initialize async integrations and finalize the context manager."""
        await self._mcp_manager.initialize()
        self._mcp_manager.register_tools(self.tool_registry)
        self._refresh_context_manager()

    def _init_web_tracking(self) -> None:
        """Initialize and inject shared web security/budget components."""
        if not hasattr(self._config, "web_search"):
            return

        search_cfg = self._config.web_search

        if hasattr(search_cfg, "security"):
            sec_cfg = search_cfg.security
            self._security_manager = WebSecurityManager(
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

        if hasattr(search_cfg, "budget"):
            budget_cfg = search_cfg.budget
            self._budget = ToolBudget(
                max_searches=budget_cfg.max_searches,
                max_results_per_search=budget_cfg.max_results_per_search,
                max_fetches=budget_cfg.max_fetches,
                max_bytes_total=budget_cfg.max_bytes_total,
            )

        for tool in self.tool_registry.get_tools():
            if isinstance(tool, WebSearchTool):
                if self._security_manager:
                    tool.set_security_manager(self._security_manager)
                if self._budget:
                    tool.set_budget(self._budget)
            elif isinstance(tool, WebFetchTool):
                if self._security_manager:
                    tool.set_security_manager(self._security_manager)
                if self._budget:
                    tool.set_budget(self._budget)

    def _init_task_management(self) -> None:
        """Initialize and inject the session task manager into task tools."""
        task_list_id = os.environ.get("PICHU_TASK_LIST_ID", f"session-{self.session_id[:8]}")

        try:
            self._task_manager = TaskManager(list_id=task_list_id)
        except Exception as exc:
            logger.warning(f"Failed to initialize task manager: {exc}")
            self._task_manager = None
            return

        for tool in self.tool_registry.get_tools():
            if isinstance(tool, (TaskCreateTool, TaskUpdateTool, TaskGetTool, TaskListTool)):
                tool.set_task_manager(self._task_manager)

    def _init_memory_management(self) -> None:
        """Initialize memory services and inject memory tooling dependencies."""
        memory_config = getattr(self._config, "memory", None)
        if memory_config and not memory_config.enabled:
            self._memory_manager = None
            self._agents_loader = None
            return

        project_root = Path(self._config.cwd) if hasattr(self._config, "cwd") else Path.cwd()
        project_name = project_root.name

        try:
            self._memory_manager = MemoryManager(
                project_root=project_root,
                project_name=project_name,
                config=memory_config,
                auto_load=True,
            )

            global_agents_path = None
            project_agents_file = "AGENTS.md"
            local_agents_file = "AGENTS.local.md"

            if memory_config:
                global_agents_path = memory_config.global_agents_path
                project_agents_file = memory_config.project_agents_file
                local_agents_file = memory_config.local_agents_file

            self._agents_loader = AgentsLoader(
                project_root=project_root,
                global_agents_path=global_agents_path,
                project_agents_file=project_agents_file,
                local_agents_file=local_agents_file,
            )
            self._agents_loader.load()

        except Exception as exc:
            logger.warning(f"Failed to initialize memory system: {exc}")
            self._memory_manager = None
            self._agents_loader = None
            return

        for tool in self.tool_registry.get_tools():
            if isinstance(tool, MemoryTool):
                tool.set_memory_manager(self._memory_manager)

    def _init_subagents(self) -> None:
        """Load sub-agent definitions and register them as runtime tools."""
        project_root = Path(self._config.cwd) if hasattr(self._config, "cwd") else Path.cwd()

        try:
            self._subagent_loader = SubAgentLoader(
                project_root=project_root,
                user_agents_dir=Path.home() / ".pichu" / "sub_agents",
            )
            self._subagent_loader.load_all()

            fallback_model = self._config.llm.model if hasattr(self._config.llm, "model") else None

            subagent_tools = create_subagent_tools(
                subagents=self._subagent_loader.get_all(),
                llm_config=self._config.llm,
                tool_registry=self.tool_registry,
                config=self._config,
                fallback_model=fallback_model,
                session_id=self.session_id,
            )

            for tool in subagent_tools:
                self.tool_registry.register_tool(tool)

            self._refresh_context_manager()

        except Exception as e:
            logger.warning(f"Failed to initialize sub-agents: {e}")
            self._subagent_loader = None

    @property
    def subagent_loader(self) -> SubAgentLoader | None:
        """Expose loaded sub-agent definitions for command/runtime callers."""
        return self._subagent_loader

    @property
    def memory_manager(self) -> MemoryManager | None:
        """Expose the memory manager for runtime integrations."""
        return self._memory_manager

    @property
    def agents_loader(self) -> AgentsLoader | None:
        """Expose AGENTS loader used to build instruction context."""
        return self._agents_loader

    def get_memory_context(self) -> str | None:
        """Return memory context text for system prompt injection."""
        if not self._memory_manager:
            return None
        return self._memory_manager.get_context_block()

    def get_agents_context(self) -> str | None:
        """Return AGENTS context text for system prompt injection."""
        if not self._agents_loader:
            return None
        context = self._agents_loader.get_context()
        return context.to_prompt_context() if context else None

    @property
    def task_manager(self) -> TaskManager | None:
        """Expose the task manager for task orchestration calls."""
        return self._task_manager

    def get_task_summary(self) -> str | None:
        """Return a compact task summary for prompt injection."""
        if not self._task_manager or self._task_manager.task_count == 0:
            return None
        return self._task_manager.get_summary()

    @property
    def security_manager(self) -> WebSecurityManager | None:
        """Expose web security manager used for URL provenance checks."""
        return self._security_manager

    @property
    def budget(self) -> ToolBudget | None:
        """Expose web usage budget shared across network tools."""
        return self._budget

    def reset_web_tracking(self) -> None:
        """Reset web provenance and budget counters for a fresh run."""
        if self._security_manager:
            self._security_manager.reset()
        if self._budget:
            self._budget.reset()

    def get_budget_status(self) -> dict[str, str] | None:
        """Return formatted budget usage values for status displays."""
        if not self._budget:
            return None
        return {
            "searches": f"{self._budget.searches_used}/{self._budget.max_searches}",
            "fetches": f"{self._budget.fetches_used}/{self._budget.max_fetches}",
            "bytes": f"{self._budget.bytes_fetched:,}/{self._budget.max_bytes_total:,}",
        }

    def increment_turn(self) -> int:
        """Increment turn counter and update session timestamp."""
        self._turn_count += 1
        self.updated_at = datetime.now()
        return self._turn_count

    @property
    def turn_count(self) -> int:
        """Return current turn count without mutating session state."""
        return self._turn_count

    @property
    def storage(self) -> SessionStorage | None:
        """Expose persistent session storage if enabled."""
        return self._storage

    @property
    def checkpoint_manager(self) -> CheckpointManager | None:
        """Expose checkpoint manager used by file-mutating tools."""
        return self._checkpoint_manager

    def save_message(
        self,
        role: str,
        content: str | None = None,
        tool_call_id: str | None = None,
        tool_calls: list[dict] | None = None,
        token_usage: dict | None = None,
    ) -> None:
        """Persist one transcript message when session storage is enabled."""
        if not self._storage:
            return
        self._ensure_session_persisted()
        entry = TranscriptEntry(
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls or [],
            timestamp=datetime.now().isoformat(),
            turn=self._turn_count,
            token_usage=token_usage,
        )
        try:
            self._storage.append_message(self.session_id, entry)
            self._storage.update_metadata(
                self.session_id,
                turn_count=self._turn_count,
                message_count=(self.context_manager.message_count if self.context_manager else 0),
            )
        except Exception as e:
            logger.warning(f"Failed to save message to transcript: {e}")

    def set_title(self, title: str) -> None:
        """Persist a human-readable title for this session."""
        if self._storage:
            self._ensure_session_persisted()
            self._storage.update_metadata(self.session_id, title=title)

    def get_title(self) -> str:
        """Return stored session title, or an empty string if unavailable."""
        if not self._storage:
            return ""
        meta = self._storage.get_metadata(self.session_id)
        return meta.title if meta else ""

    @classmethod
    def from_transcript(cls, config: Config, session_id: str) -> tuple[Session, list[TranscriptEntry]] | None:
        """Load a session shell plus transcript entries for resume flows."""
        session_cfg = getattr(config, "session", None)
        if not session_cfg or not session_cfg.enabled:
            return None

        storage_dir = Path(session_cfg.storage_dir).expanduser()
        storage = SessionStorage(
            storage_dir=storage_dir,
            cleanup_days=session_cfg.cleanup_days,
            max_sessions=session_cfg.max_sessions,
        )

        if not storage.session_exists(session_id):
            return None

        meta = storage.get_metadata(session_id)
        if not meta:
            return None

        session = cls(config, session_id=session_id)
        session._turn_count = meta.turn_count
        session.created_at = datetime.fromisoformat(meta.created_at) if meta.created_at else datetime.now()

        entries = storage.load_transcript(session_id)

        return session, entries

    def replay_transcript(self, entries: list[TranscriptEntry]) -> None:
        """Replay stored transcript entries into the active context manager."""
        if not self.context_manager:
            return
        for entry in entries:
            if entry.role == "user":
                self.context_manager.get_user_message(entry.content or "")
            elif entry.role == "assistant":
                self.context_manager.get_agent_message(
                    entry.content,
                    entry.tool_calls or None,
                )
            elif entry.role == "tool":
                if entry.tool_call_id and entry.content is not None:
                    self.context_manager.add_tool_result(
                        entry.tool_call_id,
                        entry.content,
                    )

    def fork(self, new_title: str = "") -> Session | None:
        """Create a new session branch by copying transcript and checkpoints."""
        if not self._storage:
            return None

        new_id = str(uuid.uuid4())
        cwd = str(self._config.cwd) if hasattr(self._config, "cwd") else ""
        model = self._config.model if hasattr(self._config, "model") else ""

        new_session_dir = self._storage.create_session(
            session_id=new_id,
            cwd=cwd,
            model=model,
            title=new_title or f"Fork of {self.get_title() or self.session_id[:8]}",
            parent_session_id=self.session_id,
        )

        self._storage.copy_transcript(self.session_id, new_id)

        if self._checkpoint_manager:
            self._checkpoint_manager.copy_to(new_session_dir)

        forked = Session(self._config, session_id=new_id)
        forked._turn_count = self._turn_count
        return forked

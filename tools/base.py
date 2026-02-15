from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import model_json_schema


class ToolKind(str, Enum):
    READ = "read"
    WRITE = "write"
    SHELL = "shell"
    NETWORK = "network"
    MEMORY = "memory"
    MCP = "mcp"


@dataclass
class FileDiff:
    file_path: str
    old_content: str
    new_content: str

    is_new_file: bool = False  # indicates if the file is newly created
    is_deleted: bool = False  # indicates if the file is deleted

    def create_diff_summary(self) -> str:
        """Create a simple diff summary."""
        from difflib import unified_diff

        old_lines = self.old_content.splitlines(keepends=True)
        new_lines = self.new_content.splitlines(keepends=True)

        if old_lines and not old_lines[-1].endswith("\n"):
            old_lines[-1] += "\n"
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"

        diff = unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{self.file_path}",
            tofile=f"b/{self.file_path}",
            lineterm="",
        )
        return "".join(diff)


@dataclass
class BatchFileDiff:
    """Aggregates multiple FileDiffs for batch operations."""

    diffs: list[FileDiff] = field(default_factory=list)

    def add(self, diff: FileDiff) -> None:
        self.diffs.append(diff)

    def create_diff_summary(self) -> str:
        """Combine all diffs into a single summary."""
        return "\n".join(d.create_diff_summary() for d in self.diffs if d)


@dataclass
class OperationResult:
    """Result of a single operation in a batch."""

    operation_index: int
    path: str
    success: bool
    message: str
    error: str | None = None


@dataclass
class ToolInvocation:
    cwd: Path
    params: dict[str, Any]


@dataclass
class ToolResult:
    success: bool
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    truncated: bool = False  # indicates if output was truncated
    diff: FileDiff | None = None  # for file modification tools
    batch_diff: BatchFileDiff | None = None  # for batch operations
    operation_results: list[OperationResult] | None = None  # per-operation results

    @classmethod
    def error_result(cls, error_message: str, output: str = "", **kwargs: Any) -> ToolResult:
        return cls(success=False, error=error_message, output=output, **kwargs)

    @classmethod
    def success_result(cls, output: str, **kwargs: Any) -> ToolResult:
        return cls(success=True, output=output, error=None, **kwargs)

    def to_model_output(self) -> str:
        if self.success:
            return self.output or ""
        else:
            return f"Error: {self.error}\n output: {self.output}"


@dataclass
class ToolConfirmation:
    tool_name: str
    tool_kind: ToolKind
    params: dict[str, Any]
    description: str = "This tool invocation requires confirmation."
    diff: FileDiff | None = None
    affected_paths: list[str] = field(default_factory=list)
    command: str | None = None
    is_dangerous: bool = False


class Tool(ABC):
    name: str = "base_tool"
    description: str = "Base tool description"
    kind: ToolKind = ToolKind.READ

    def __init__(self, config: Any = None) -> None:
        self._config = config

    @property
    def schema(self) -> dict[str, Any] | type[BaseModel]:
        raise NotImplementedError("Tool must implement schema property or class attribute.")

    @abstractmethod
    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        pass

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        schema = self.schema

        if isinstance(schema, type) and issubclass(schema, BaseModel):
            try:
                schema(**params)
                return []
            except ValidationError as e:
                errors = []
                for err in e.errors():
                    loc = " -> ".join(str(location_item) for location_item in err.get("loc", []))
                    msg = err.get("msg", "validation error")
                    errors.append(f"Parameter '{loc}': {msg}")

                return errors

            except Exception as e:
                return [str(e)]

        return []

    def is_mutating(self, params: dict[str, Any]) -> bool:
        return self.kind in {
            ToolKind.WRITE,
            ToolKind.SHELL,
            ToolKind.NETWORK,
            ToolKind.MEMORY,
            ToolKind.MCP,
        }

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        """Return a ToolConfirmation if this invocation needs user approval.

        Override in subclasses to provide tool-specific details (diff, paths, etc.).
        Returns None if the tool does not require confirmation.
        """
        if not self.is_mutating(invocation.params):
            return None

        return ToolConfirmation(
            tool_name=self.name,
            tool_kind=self.kind,
            description=f"Tool '{self.name}' wants to perform a mutating operation.",
            params=invocation.params,
        )

    def to_openai_schema(self) -> dict[str, Any]:
        schema = self.schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            json_schema = model_json_schema(schema, mode="serialization")

            return {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": json_schema.get("properties", {}),
                    "required": json_schema.get("required", []),
                },
            }
        elif isinstance(schema, dict):
            result: dict[str, Any] = {
                "name": self.name,
                "description": self.description,
            }

            if "parameters" in schema:
                result["parameters"] = schema["parameters"]
            else:
                result["parameters"] = schema

            return result
        else:
            raise ValueError(f"Invalid schema type for {self.name} : {type(schema)}")

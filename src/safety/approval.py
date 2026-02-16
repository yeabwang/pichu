"""Approval / permission system for mutating tool execution."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Awaitable, Callable

from config.config import ApprovalConfig, ApprovalPolicy
from tools.base import ToolConfirmation, ToolKind
from utils.runtime_logging import audit_event

from safety.approval_types import ApprovalDecision, ApprovalResponse
from safety.command_safety import (
    command_fingerprint,
    is_dangerous_command,
    is_safe_command,
)
from safety.rule_engine import evaluate_rules

logger = logging.getLogger(__name__)


class ApprovalManager:
    """Central approval manager for tool execution.

    Evaluation order:
    1. Policy short-circuits (YOLO, PLAN)
    2. Permission rules (deny → ask → allow)
    3. Session approval memory
    4. Dangerous command checks
    5. Tool-marked danger checks
    6. Policy-based default decision
    7. Async user confirmation callback
    """

    def __init__(
        self,
        config: ApprovalConfig,
        cwd: Path,
        confirmation_callback: (Callable[[ToolConfirmation], Awaitable[ApprovalResponse]] | None) = None,
    ) -> None:
        self._config = config
        self._cwd = cwd
        self._confirmation_callback = confirmation_callback
        self._session_approvals: set[str] = set()
        self._session_denials: set[str] = set()

    def set_confirmation_callback(
        self,
        callback: Callable[[ToolConfirmation], Awaitable[ApprovalResponse]],
    ) -> None:
        self._confirmation_callback = callback

    @property
    def policy(self) -> ApprovalPolicy:
        return self._config.policy

    async def check_and_approve(self, confirmation: ToolConfirmation) -> ApprovalDecision:
        decision = self._evaluate(confirmation)
        if decision == ApprovalDecision.NEEDS_CONFIRMATION:
            approved = await self._request_confirmation(confirmation)
            decision = ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED
        if decision == ApprovalDecision.REJECTED:
            audit_event(
                "approval.denied",
                "Tool execution denied by approval policy",
                tool_name=confirmation.tool_name,
                tool_kind=confirmation.tool_kind.value,
                policy=self._config.policy.value,
                is_dangerous=confirmation.is_dangerous,
                command=confirmation.command or "",
            )
        return decision

    def _evaluate(self, confirmation: ToolConfirmation) -> ApprovalDecision:
        policy = self._config.policy

        if policy == ApprovalPolicy.YOLO:
            return ApprovalDecision.APPROVED
        if policy == ApprovalPolicy.PLAN:
            return ApprovalDecision.REJECTED

        if self._config.rules:
            rule_decision = evaluate_rules(self._config.rules, confirmation)
            if rule_decision is not None:
                return rule_decision

        memory_key = self._make_memory_key(confirmation)
        if memory_key in self._session_approvals:
            return ApprovalDecision.APPROVED
        if memory_key in self._session_denials:
            return ApprovalDecision.REJECTED

        if confirmation.command and is_dangerous_command(confirmation.command):
            confirmation.is_dangerous = True
            if policy != ApprovalPolicy.YOLO:
                return ApprovalDecision.REJECTED

        if confirmation.is_dangerous:
            return ApprovalDecision.NEEDS_CONFIRMATION

        return self._policy_decision(confirmation)

    def _policy_decision(self, confirmation: ToolConfirmation) -> ApprovalDecision:
        policy = self._config.policy
        kind = confirmation.tool_kind

        if policy == ApprovalPolicy.NEVER:
            return ApprovalDecision.REJECTED

        if policy in {ApprovalPolicy.AUTO, ApprovalPolicy.ON_FAILURE}:
            if confirmation.command and not is_safe_command(confirmation.command):
                return ApprovalDecision.NEEDS_CONFIRMATION
            return ApprovalDecision.APPROVED

        if policy == ApprovalPolicy.AUTO_EDIT:
            if kind == ToolKind.WRITE:
                return ApprovalDecision.APPROVED
            if kind in {ToolKind.SHELL, ToolKind.NETWORK, ToolKind.MCP}:
                if confirmation.command and is_safe_command(confirmation.command):
                    return ApprovalDecision.APPROVED
                return ApprovalDecision.NEEDS_CONFIRMATION
            return ApprovalDecision.APPROVED

        if kind in {ToolKind.READ}:
            return ApprovalDecision.APPROVED

        return ApprovalDecision.NEEDS_CONFIRMATION

    async def _request_confirmation(self, confirmation: ToolConfirmation) -> bool:
        if not self._confirmation_callback:
            logger.warning(
                "No confirmation callback set; rejecting %s",
                confirmation.tool_name,
            )
            return False

        response = await self._confirmation_callback(confirmation)
        memory_key = self._make_memory_key(confirmation)

        if response == ApprovalResponse.ALWAYS:
            self._session_approvals.add(memory_key)
        elif response == ApprovalResponse.NO and self._config.remember_session:
            self._session_denials.add(memory_key)

        return response in {ApprovalResponse.YES, ApprovalResponse.ALWAYS}

    @staticmethod
    def _make_memory_key(confirmation: ToolConfirmation) -> str:
        if confirmation.command:
            return f"{confirmation.tool_name}:{command_fingerprint(confirmation.command)}"
        return confirmation.tool_name


__all__ = [
    "ApprovalDecision",
    "ApprovalManager",
    "ApprovalResponse",
    "is_dangerous_command",
    "is_safe_command",
]

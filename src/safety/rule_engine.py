"""Permission rule parsing and evaluation for approval decisions."""

from __future__ import annotations

import fnmatch
import logging

from config.config import PermissionRulesConfig
from safety.approval_types import ApprovalDecision
from tools.base import ToolConfirmation

logger = logging.getLogger(__name__)


def parse_rule(rule: str) -> tuple[str, str | None]:
    """Parse a permission rule into (tool_name, optional_specifier_glob)."""
    rule = rule.strip()
    paren_open = rule.find("(")
    if paren_open == -1:
        return rule, None

    paren_close = rule.rfind(")")
    if paren_close == -1:
        return rule, None

    tool_name = rule[:paren_open].strip()
    specifier = rule[paren_open + 1 : paren_close].strip()
    return tool_name, specifier if specifier else None


def get_specifier_for_tool(confirmation: ToolConfirmation) -> str:
    """Extract a comparable specifier used by permission globs."""
    if confirmation.command:
        return confirmation.command

    path = confirmation.params.get("path", "")
    if path:
        return path

    file_path = confirmation.params.get("file_path", "")
    if file_path:
        return file_path

    url = confirmation.params.get("url", "")
    if url:
        return url

    return " ".join(str(v) for v in confirmation.params.values())


def match_rule(rule: str, confirmation: ToolConfirmation) -> bool:
    """Check whether a rule matches the provided tool confirmation payload."""
    rule_tool, rule_spec = parse_rule(rule)
    if rule_tool != confirmation.tool_name:
        return False

    if rule_spec is None:
        return True

    specifier = get_specifier_for_tool(confirmation)
    return fnmatch.fnmatch(specifier, rule_spec)


def evaluate_rules(rules: PermissionRulesConfig, confirmation: ToolConfirmation) -> ApprovalDecision | None:
    """Evaluate deny → ask → allow rules. Returns None when no rules match."""
    for rule in rules.deny:
        if match_rule(rule, confirmation):
            logger.debug(f"Deny rule matched: {rule}")
            return ApprovalDecision.REJECTED

    for rule in rules.ask:
        if match_rule(rule, confirmation):
            logger.debug(f"Ask rule matched: {rule}")
            return ApprovalDecision.NEEDS_CONFIRMATION

    for rule in rules.allow:
        if match_rule(rule, confirmation):
            logger.debug(f"Allow rule matched: {rule}")
            return ApprovalDecision.APPROVED

    return None

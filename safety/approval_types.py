"""Shared enums used by the approval pipeline."""

from __future__ import annotations

from enum import Enum


class ApprovalDecision(Enum):
    """Outcome of an approval check."""

    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CONFIRMATION = "needs_confirmation"


class ApprovalResponse(Enum):
    """User's response to an approval prompt."""

    YES = "yes"  # Approve this one invocation
    NO = "no"  # Deny this one invocation
    ALWAYS = "always"  # Approve and remember for the session

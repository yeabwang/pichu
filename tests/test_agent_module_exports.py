"""Tests for the public `agent` module export surface."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_agent_module_exports_core_types():
    import agent

    assert agent.Agent is not None
    assert agent.Session is not None
    assert agent.AgentEvent is not None
    assert agent.AgentEventType is not None

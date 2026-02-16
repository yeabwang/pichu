"""Focused tests for prompt construction helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from prompts.system import create_loop_breaker_prompt, get_compression_prompt, get_system_prompt


def test_get_system_prompt_includes_core_and_optional_sections(tmp_path):
    cfg = Config(cwd=tmp_path)
    cfg.developer_instructions = "Developer constraints."
    cfg.user_instructions = "User preferences."

    prompt = get_system_prompt(
        config=cfg,
        agents_context="AGENTS CONTEXT",
        memory_context="MEMORY CONTEXT",
    )

    assert "# Identity" in prompt
    assert "# Environment" in prompt
    assert "# Security Guidelines" in prompt
    assert "# Operational Guidelines" in prompt
    assert "AGENTS CONTEXT" in prompt
    assert "MEMORY CONTEXT" in prompt
    assert "Developer constraints." in prompt
    assert "User preferences." in prompt


def test_prompt_helper_templates_include_expected_markers():
    compression_prompt = get_compression_prompt()
    loop_prompt = create_loop_breaker_prompt("Repeated identical tool calls")

    assert "## ORIGINAL GOAL" in compression_prompt
    assert "## NEXT STEP" in compression_prompt
    assert "[SYSTEM NOTICE: Loop Detected]" in loop_prompt
    assert "Repeated identical tool calls" in loop_prompt

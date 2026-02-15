"""Tests for the public client module and stream-event contracts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import client
from client.llm_client import LLMClient
from client.models import (
    StreamEvent,
    StreamEventType,
    ToolResultMessage,
    parse_tool_call_arguments,
)
from config import LLMConfig, RetryConfig


def test_client_module_exports_core_types():
    assert client.LLMClient is not None
    assert client.StreamEvent is not None
    assert client.StreamEventType is not None


def test_stream_event_from_error_sets_error_field():
    event = StreamEvent.from_error("boom")

    assert event.type == StreamEventType.ERROR
    assert event.error == "boom"
    assert event.data == {"error": "boom"}


def test_parse_tool_call_arguments_preserves_raw_on_invalid_payloads():
    assert parse_tool_call_arguments('{"ok": true}') == {"ok": True}
    assert parse_tool_call_arguments("[1, 2]") == {"raw_arguments": "[1, 2]"}
    assert parse_tool_call_arguments("") == {}


def test_tool_result_message_schema_alias_is_compatible():
    message = ToolResultMessage(tool_call_id="call_1", content="done")
    assert message.to_openai_schema() == message.to_open_ai_schema()


def test_build_completion_kwargs_includes_runtime_config():
    client_instance = LLMClient(
        LLMConfig(
            api_key="test-key",
            base_url="https://example.com",
            model="test-model",
            temperature=0.4,
            timeout=5.0,
            max_tokens=321,
            retry=RetryConfig(max_attempts=2, min_wait=0.1, max_wait=1.0, multiplier=1),
        )
    )
    kwargs = client_instance._build_completion_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
        tools=[{"name": "echo", "description": "Echo", "parameters": {"type": "object"}}],
    )

    assert kwargs["temperature"] == 0.4
    assert kwargs["max_tokens"] == 321
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["stream_options"] == {"include_usage": True}


def test_build_tool_schema_rejects_missing_tool_name():
    client_instance = LLMClient(
        LLMConfig(
            api_key="test-key",
            base_url="https://example.com",
            model="test-model",
            retry=RetryConfig(max_attempts=1, min_wait=0.1, max_wait=0.1, multiplier=1),
        )
    )

    with pytest.raises(ValueError, match="name"):
        client_instance._build_tool_schema([{"description": "missing name"}])

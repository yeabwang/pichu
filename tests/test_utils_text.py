from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import utils.text as text_module


def test_truncate_text_does_not_skip_truncation_for_non_ascii_with_short_char_length(monkeypatch):
    text = "😀😀😀"

    def _fake_count_tokens(value: str, model: str = "gpt-3.5-turbo") -> int:
        if value == text:
            return 9
        if value == "\n...[truncated]":
            return 1
        return len(value)

    def _fake_truncate_by_chars(value: str, target_tokens: int, suffix: str, model: str) -> str:
        assert value == text
        assert target_tokens == 2
        return "ok" + suffix

    monkeypatch.setattr(text_module, "count_tokens", _fake_count_tokens)
    monkeypatch.setattr(text_module, "_truncate_by_chars", _fake_truncate_by_chars)

    result = text_module.truncate_text_to_token_limit(text, max_tokens=3, preserve_lines=False)
    assert result == "ok\n...[truncated]"

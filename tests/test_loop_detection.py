"""Tests for the loop detection system.

Covers all 5 detection strategies, the configuration, reset/suppress
behaviour, and edge cases.
"""

from __future__ import annotations

from utils.loop_detector import (
    LoopAction,
    LoopDetectionConfig,
    LoopDetector,
    LoopStrategy,
    TurnRecord,
    _hash_args,
    _hash_text,
    _normalize_error,
    _text_similarity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_turn(
    turn: int,
    tool_calls: list[tuple[str, dict]] | None = None,
    response_text: str = "",
    errors: list[str] | None = None,
) -> TurnRecord:
    """Build a TurnRecord with hashed tool args."""
    tc = [(name, _hash_args(args)) for name, args in (tool_calls or [])]
    return TurnRecord(
        turn_number=turn,
        tool_calls=tc,
        response_text=response_text,
        response_hash=_hash_text(response_text) if response_text else "",
        errors=errors or [],
    )


# ===================================================================
#  Utility function tests
# ===================================================================


class TestUtilityFunctions:
    def test_hash_args_deterministic(self):
        h1 = _hash_args({"a": 1, "b": 2})
        h2 = _hash_args({"b": 2, "a": 1})
        assert h1 == h2

    def test_hash_args_different(self):
        h1 = _hash_args({"a": 1})
        h2 = _hash_args({"a": 2})
        assert h1 != h2

    def test_hash_text(self):
        h1 = _hash_text("hello world")
        h2 = _hash_text("hello world")
        assert h1 == h2
        assert len(h1) == 16

    def test_text_similarity_identical(self):
        assert _text_similarity("hello", "hello") == 1.0

    def test_text_similarity_totally_different(self):
        sim = _text_similarity("aaa", "zzz")
        assert sim < 0.5

    def test_text_similarity_empty(self):
        assert _text_similarity("", "hello") == 0.0
        assert _text_similarity("hello", "") == 0.0

    def test_normalize_error(self):
        assert _normalize_error("  Error:  File  Not Found  ") == "error: file not found"


# ===================================================================
#  Configuration tests
# ===================================================================


class TestLoopDetectionConfig:
    def test_defaults(self):
        cfg = LoopDetectionConfig()
        assert cfg.enabled is True
        assert cfg.max_repeated_tool_calls == 3
        assert cfg.similarity_threshold == 0.85
        assert cfg.action == "warn_and_nudge"

    def test_action_enum_valid(self):
        cfg = LoopDetectionConfig(action="stop")
        assert cfg.action_enum == LoopAction.STOP

    def test_action_enum_invalid_falls_back(self):
        cfg = LoopDetectionConfig(action="unknown_action")
        assert cfg.action_enum == LoopAction.WARN_AND_NUDGE

    def test_custom_values(self):
        cfg = LoopDetectionConfig(
            enabled=False,
            max_repeated_tool_calls=5,
            window_size=20,
            action="pause",
        )
        assert cfg.enabled is False
        assert cfg.max_repeated_tool_calls == 5
        assert cfg.window_size == 20
        assert cfg.action_enum == LoopAction.PAUSE


# ===================================================================
#  LoopDetector core behaviour tests
# ===================================================================


class TestLoopDetectorCore:
    def test_no_detection_with_fewer_than_2_turns(self):
        d = LoopDetector()
        d.record_turn(_make_turn(1, [("shell", {"cmd": "ls"})]))
        assert d.check() is None

    def test_no_detection_when_disabled(self):
        d = LoopDetector(LoopDetectionConfig(enabled=False))
        for i in range(5):
            d.record_turn(_make_turn(i, [("shell", {"cmd": "ls"})]))
        assert d.check() is None

    def test_reset_clears_history(self):
        d = LoopDetector()
        for i in range(5):
            d.record_turn(_make_turn(i, [("shell", {"cmd": "ls"})]))
        d.reset()
        assert d.history_len == 0
        assert d.check() is None

    def test_suppression_after_detection(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=2))
        for i in range(3):
            d.record_turn(_make_turn(i, [("shell", {"cmd": "ls"})]))

        result = d.check()
        assert result is not None

        # Second check should be suppressed
        assert d.check() is None

    def test_unsuppress_allows_redetection(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=2))
        for i in range(3):
            d.record_turn(_make_turn(i, [("shell", {"cmd": "ls"})]))

        result = d.check()
        assert result is not None

        d.unsuppress()
        # Add more turns to trigger again
        d.record_turn(_make_turn(4, [("shell", {"cmd": "ls"})]))
        result2 = d.check()
        assert result2 is not None


# ===================================================================
#  Strategy 1: Repeated Tool Calls
# ===================================================================


class TestRepeatedToolCalls:
    def test_detects_repeated_tool_calls(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=3))
        for i in range(3):
            d.record_turn(_make_turn(i, [("write_file", {"path": "/tmp/x.py", "content": "abc"})]))

        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.REPEATED_TOOL_CALLS
        assert result.turn_count == 3
        assert "write_file" in result.description

    def test_no_detection_below_threshold(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=3))
        for i in range(2):
            d.record_turn(_make_turn(i, [("write_file", {"path": "/tmp/x.py", "content": "abc"})]))
        assert d.check() is None

    def test_different_args_not_detected(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=3))
        for i in range(5):
            d.record_turn(_make_turn(i, [("write_file", {"path": f"/tmp/{i}.py", "content": "abc"})]))
        assert d.check() is None

    def test_mixed_tools_no_detection(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=3))
        d.record_turn(_make_turn(0, [("shell", {"cmd": "ls"})]))
        d.record_turn(_make_turn(1, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(2, [("write_file", {"path": "b.py", "content": "x"})]))
        assert d.check() is None

    def test_detects_across_multi_tool_turns(self):
        """Same tool+args embedded among other calls still detected."""
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=3))
        for i in range(3):
            d.record_turn(
                _make_turn(
                    i,
                    [
                        ("read_file", {"path": "a.py"}),
                        ("shell", {"cmd": "ls"}),  # repeated
                    ],
                )
            )
        result = d.check()
        assert result is not None

    def test_uses_trailing_window_only(self):
        cfg = LoopDetectionConfig(
            window_size=3,
            max_repeated_tool_calls=3,
            max_repeated_errors=10,
            max_similar_responses=10,
        )
        d = LoopDetector(cfg)

        # Older repeated calls should not trigger once outside trailing window.
        d.record_turn(_make_turn(0, [("shell", {"cmd": "ls"})]))
        d.record_turn(_make_turn(1, [("shell", {"cmd": "ls"})]))
        d.record_turn(_make_turn(2, [("shell", {"cmd": "ls"})]))

        # The active window now has diverse actions.
        d.record_turn(_make_turn(3, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(4, [("write_file", {"path": "b.py", "content": "x"})]))
        d.record_turn(_make_turn(5, [("grep", {"pattern": "foo"})]))

        assert d.check() is None


# ===================================================================
#  Strategy 2: Repeated Errors
# ===================================================================


class TestRepeatedErrors:
    def test_detects_repeated_errors(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_errors=3))
        for i in range(3):
            d.record_turn(_make_turn(i, [("shell", {"cmd": f"test{i}"})], errors=["File not found"]))
        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.REPEATED_ERRORS
        assert "file not found" in result.description.lower()

    def test_no_detection_different_errors(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_errors=3))
        d.record_turn(_make_turn(0, errors=["Error A"]))
        d.record_turn(_make_turn(1, errors=["Error B"]))
        d.record_turn(_make_turn(2, errors=["Error C"]))
        assert d.check() is None

    def test_normalizes_whitespace(self):
        d = LoopDetector(LoopDetectionConfig(max_repeated_errors=3))
        d.record_turn(_make_turn(0, errors=["File  not   found"]))
        d.record_turn(_make_turn(1, errors=["file not found"]))
        d.record_turn(_make_turn(2, errors=["FILE NOT FOUND"]))
        result = d.check()
        assert result is not None


# ===================================================================
#  Strategy 3: Response Similarity
# ===================================================================


class TestResponseSimilarity:
    def test_detects_identical_responses(self):
        d = LoopDetector(LoopDetectionConfig(max_similar_responses=3, similarity_threshold=0.85))
        text = "I'll try to fix the issue by editing the file."
        for i in range(3):
            d.record_turn(_make_turn(i, response_text=text))
        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.RESPONSE_SIMILARITY

    def test_detects_near_identical_responses(self):
        d = LoopDetector(LoopDetectionConfig(max_similar_responses=3, similarity_threshold=0.85))
        d.record_turn(_make_turn(0, response_text="I'll fix this by editing the config file now."))
        d.record_turn(_make_turn(1, response_text="I'll fix this by editing the config file here."))
        d.record_turn(_make_turn(2, response_text="I'll fix this by editing the config file again."))
        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.RESPONSE_SIMILARITY

    def test_no_detection_for_different_responses(self):
        d = LoopDetector(LoopDetectionConfig(max_similar_responses=3, similarity_threshold=0.85))
        d.record_turn(_make_turn(0, response_text="Let me read the file first."))
        d.record_turn(_make_turn(1, response_text="Now I see the issue — missing import."))
        d.record_turn(_make_turn(2, response_text="Fixed! The tests should pass now."))
        assert d.check() is None

    def test_empty_responses_ignored(self):
        d = LoopDetector(LoopDetectionConfig(max_similar_responses=3))
        d.record_turn(_make_turn(0, response_text=""))
        d.record_turn(_make_turn(1, response_text=""))
        d.record_turn(_make_turn(2, response_text=""))
        assert d.check() is None


# ===================================================================
#  Strategy 4: Oscillation
# ===================================================================


class TestOscillation:
    def test_detects_ab_ab_pattern(self):
        d = LoopDetector()
        d.record_turn(_make_turn(0, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(1, [("write_file", {"path": "a.py", "content": "x"})]))
        d.record_turn(_make_turn(2, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(3, [("write_file", {"path": "a.py", "content": "x"})]))
        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.OSCILLATION

    def test_no_detection_for_abab_with_different_args(self):
        d = LoopDetector()
        d.record_turn(_make_turn(0, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(1, [("write_file", {"path": "a.py", "content": "x"})]))
        d.record_turn(_make_turn(2, [("read_file", {"path": "b.py"})]))  # different file
        d.record_turn(_make_turn(3, [("write_file", {"path": "b.py", "content": "y"})]))
        assert d.check() is None

    def test_no_detection_for_fewer_than_4_turns(self):
        d = LoopDetector()
        d.record_turn(_make_turn(0, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(1, [("write_file", {"path": "a.py", "content": "x"})]))
        d.record_turn(_make_turn(2, [("read_file", {"path": "a.py"})]))
        assert d.check() is None

    def test_same_action_repeated_not_oscillation(self):
        """A-A-A-A is not oscillation (it's repeated tool calls)."""
        d = LoopDetector(LoopDetectionConfig(max_repeated_tool_calls=10))  # high threshold
        for i in range(4):
            d.record_turn(_make_turn(i, [("shell", {"cmd": "ls"})]))
        result = d.check()
        # Should not be detected as oscillation (A==B check fails)
        if result:
            assert result.strategy != LoopStrategy.OSCILLATION


# ===================================================================
#  Strategy 5: Progress Stall
# ===================================================================


class TestProgressStall:
    def test_detects_stall(self):
        """Window full of the same 1–2 actions, preceded by earlier history."""
        cfg = LoopDetectionConfig(window_size=4, max_repeated_tool_calls=10, max_repeated_errors=10)
        d = LoopDetector(cfg)

        # Earlier diverse history (within deque since maxlen=window_size=4,
        # we need to record earlier turns, then fill window with stale ones)
        # Increase window to 5 so earlier turns stay in deque
        cfg2 = LoopDetectionConfig(window_size=5, max_repeated_tool_calls=20, max_repeated_errors=10)
        d = LoopDetector(cfg2)

        # 3 diverse earlier turns
        d.record_turn(_make_turn(0, [("read_file", {"path": "a.py"})]))
        d.record_turn(_make_turn(1, [("write_file", {"path": "b.py", "content": "x"})]))
        d.record_turn(_make_turn(2, [("shell", {"cmd": "pytest"})]))

        # Now fill window_size=5 turns with the same single action
        for i in range(3, 8):
            d.record_turn(_make_turn(i, [("shell", {"cmd": "ls"})]))

        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.PROGRESS_STALL

    def test_no_stall_with_diverse_actions(self):
        cfg = LoopDetectionConfig(window_size=3, max_repeated_tool_calls=10)
        d = LoopDetector(cfg)
        # Earlier history
        d.record_turn(_make_turn(0, [("shell", {"cmd": "ls"})]))
        d.record_turn(_make_turn(1, [("shell", {"cmd": "ls"})]))
        d.record_turn(_make_turn(2, [("shell", {"cmd": "ls"})]))
        # Window with new actions
        d.record_turn(_make_turn(3, [("read_file", {"path": "new.py"})]))
        d.record_turn(_make_turn(4, [("write_file", {"path": "out.py", "content": "y"})]))
        d.record_turn(_make_turn(5, [("grep", {"pattern": "fix"})]))
        assert d.check() is None

    def test_no_stall_when_window_not_full(self):
        cfg = LoopDetectionConfig(window_size=10, max_repeated_tool_calls=10)
        d = LoopDetector(cfg)
        for i in range(5):
            d.record_turn(_make_turn(i, [("shell", {"cmd": f"cmd_{i}"})]))
        assert d.check() is None  # only 5 < window_size=10


# ===================================================================
#  Priority / First-match ordering
# ===================================================================


class TestStrategyPriority:
    def test_repeated_tools_takes_priority_over_similarity(self):
        """When both strategies would trigger, repeated_tool_calls wins."""
        d = LoopDetector(
            LoopDetectionConfig(
                max_repeated_tool_calls=3,
                max_similar_responses=3,
                similarity_threshold=0.85,
            )
        )
        text = "I'll fix this error now."
        for i in range(3):
            d.record_turn(
                _make_turn(
                    i,
                    tool_calls=[("shell", {"cmd": "npm test"})],
                    response_text=text,
                )
            )
        result = d.check()
        assert result is not None
        assert result.strategy == LoopStrategy.REPEATED_TOOL_CALLS


# ===================================================================
#  LoopDetectionResult tests
# ===================================================================


class TestLoopDetectionResult:
    def test_nudge_message(self):
        from utils.loop_detector import LoopDetectionResult

        r = LoopDetectionResult(
            strategy=LoopStrategy.REPEATED_TOOL_CALLS,
            description="Tool 'shell' called 3 times.",
            turn_count=3,
            action=LoopAction.WARN_AND_NUDGE,
        )
        msg = r.nudge_message
        assert "Loop detected" in msg
        assert "repeated_tool_calls" in msg
        assert "fundamentally different approach" in msg


# ===================================================================
#  Config integration test
# ===================================================================


class TestConfigIntegration:
    def test_loop_detection_config_in_main_config(self):
        from config.config import Config
        from config.config import LoopDetectionConfig as LDConfig

        c = Config()
        assert isinstance(c.loop_detection, LDConfig)
        assert c.loop_detection.enabled is True

    def test_update_from_dict(self):
        from config.config import Config

        c = Config()
        c.update_from_dict(
            {
                "loop_detection": {
                    "enabled": False,
                    "max_repeated_tool_calls": 5,
                    "action": "stop",
                }
            }
        )
        assert c.loop_detection.enabled is False
        assert c.loop_detection.max_repeated_tool_calls == 5
        assert c.loop_detection.action == "stop"


# ===================================================================
#  Event type test
# ===================================================================


class TestLoopDetectedEvent:
    def test_event_creation(self):
        from agent.events import AgentEvent, AgentEventType

        e = AgentEvent.loop_detected(
            strategy="repeated_tool_calls",
            description="Tool 'shell' called 3 times.",
            turn_count=3,
            action_taken="warn_and_nudge",
        )
        assert e.type == AgentEventType.LOOP_DETECTED
        assert e.data["strategy"] == "repeated_tool_calls"
        assert e.data["turn_count"] == 3
        assert e.data["action_taken"] == "warn_and_nudge"

"""Tests for the hooks system."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from hooks.engine import HookEngine
from hooks.types import (
    _BLOCKING_EVENTS,
    HookEvent,
    HookFireResult,
    HookHandler,
    HookInput,
    HookMatcher,
    HookResult,
)

# =====================================================================
# HookHandler tests
# =====================================================================


class TestHookHandler:
    def test_valid_command_handler(self):
        h = HookHandler(type="command", command="echo hello")
        assert h.type == "command"
        assert h.command == "echo hello"
        assert h.timeout == 60

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid hook type"):
            HookHandler(type="invalid", command="echo hello")

    def test_empty_command_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            HookHandler(type="command", command="")

    def test_default_timeout(self):
        h = HookHandler(type="command", command="echo hi")
        assert h.timeout == 60

    def test_custom_timeout(self):
        h = HookHandler(type="command", command="echo hi", timeout=120)
        assert h.timeout == 120


# =====================================================================
# HookInput tests
# =====================================================================


class TestHookInput:
    def test_to_json_dict(self):
        inp = HookInput(
            session_id="abc123",
            cwd="/tmp",
            hook_event_name="PreToolUse",
            permission_mode="on_request",
            extra={"tool_name": "shell", "tool_input": {"command": "ls"}},
        )
        d = inp.to_json_dict()
        assert d["session_id"] == "abc123"
        assert d["hook_event_name"] == "PreToolUse"
        assert d["tool_name"] == "shell"
        assert d["tool_input"]["command"] == "ls"

    def test_empty_extra(self):
        inp = HookInput(session_id="x", cwd="/", hook_event_name="Stop")
        d = inp.to_json_dict()
        assert "tool_name" not in d


# =====================================================================
# HookResult tests
# =====================================================================


class TestHookResult:
    def test_success(self):
        r = HookResult(exit_code=0)
        assert r.is_success
        assert not r.is_blocking_error
        assert not r.is_non_blocking_error

    def test_blocking_error(self):
        r = HookResult(exit_code=2, stderr="blocked!")
        assert not r.is_success
        assert r.is_blocking_error

    def test_non_blocking_error(self):
        r = HookResult(exit_code=1, stderr="warning")
        assert r.is_non_blocking_error

    def test_permission_decision_properties(self):
        r = HookResult(
            exit_code=0,
            hook_specific_output={
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "too dangerous",
            },
        )
        assert r.permission_decision == "deny"
        assert r.permission_decision_reason == "too dangerous"

    def test_updated_input(self):
        r = HookResult(
            exit_code=0,
            hook_specific_output={
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"command": "ls -la"},
            },
        )
        assert r.updated_input == {"command": "ls -la"}


# =====================================================================
# HookFireResult tests
# =====================================================================


class TestHookFireResult:
    def test_empty_results(self):
        fr = HookFireResult(event=HookEvent.PRE_TOOL_USE)
        assert not fr.any_blocking
        assert not fr.any_deny
        assert not fr.any_allow
        assert not fr.should_block
        assert not fr.should_stop

    def test_deny_detection(self):
        r = HookResult(
            exit_code=0,
            hook_specific_output={
                "permissionDecision": "deny",
                "permissionDecisionReason": "nope",
            },
        )
        fr = HookFireResult(event=HookEvent.PRE_TOOL_USE, results=[r])
        assert fr.any_deny
        assert not fr.any_allow

    def test_allow_detection(self):
        r = HookResult(
            exit_code=0,
            hook_specific_output={"permissionDecision": "allow"},
        )
        fr = HookFireResult(event=HookEvent.PRE_TOOL_USE, results=[r])
        assert fr.any_allow
        assert not fr.any_deny

    def test_blocking_exit_code(self):
        r = HookResult(exit_code=2, stderr="blocked by hook")
        fr = HookFireResult(event=HookEvent.PRE_TOOL_USE, results=[r])
        assert fr.any_blocking
        assert fr.should_block
        assert fr.blocking_stderr == "blocked by hook"

    def test_block_decision(self):
        r = HookResult(exit_code=0, decision="block", reason="tests failing")
        fr = HookFireResult(event=HookEvent.STOP, results=[r])
        assert fr.should_block
        assert fr.blocking_reason == "tests failing"

    def test_stop_signal(self):
        r = HookResult(exit_code=0, continue_=False, stop_reason="build failed")
        fr = HookFireResult(event=HookEvent.STOP, results=[r])
        assert fr.should_stop
        assert fr.stop_reason == "build failed"

    def test_collect_additional_context(self):
        r1 = HookResult(exit_code=0, additional_context="ctx1")
        r2 = HookResult(exit_code=0, additional_context="ctx2")
        r3 = HookResult(exit_code=1)  # non-success, should be excluded
        fr = HookFireResult(event=HookEvent.SESSION_START, results=[r1, r2, r3])
        ctx = fr.collect_additional_context()
        assert ctx == "ctx1\nctx2"


# =====================================================================
# HookEngine matching tests
# =====================================================================


class TestHookEngineMatching:
    def _engine(self) -> HookEngine:
        return HookEngine(session_id="test", cwd="/tmp")

    def test_no_pattern_matches_all(self):
        e = self._engine()
        assert e._matches(None, "shell", HookEvent.PRE_TOOL_USE)
        assert e._matches("", "shell", HookEvent.PRE_TOOL_USE)
        assert e._matches("*", "shell", HookEvent.PRE_TOOL_USE)

    def test_exact_match(self):
        e = self._engine()
        assert e._matches("shell", "shell", HookEvent.PRE_TOOL_USE)
        assert not e._matches("shell", "write_file", HookEvent.PRE_TOOL_USE)

    def test_regex_match(self):
        e = self._engine()
        assert e._matches("write_file|edit_file", "write_file", HookEvent.PRE_TOOL_USE)
        assert e._matches("write_file|edit_file", "edit_file", HookEvent.PRE_TOOL_USE)
        assert not e._matches("write_file|edit_file", "shell", HookEvent.PRE_TOOL_USE)

    def test_regex_wildcard(self):
        e = self._engine()
        assert e._matches("mcp__.*", "mcp__memory__create", HookEvent.PRE_TOOL_USE)
        assert not e._matches("mcp__.*", "shell", HookEvent.PRE_TOOL_USE)

    def test_non_matcher_events_always_match(self):
        e = self._engine()
        # UserPromptSubmit doesn't support matchers
        assert e._matches("anything", "anything", HookEvent.USER_PROMPT_SUBMIT)
        assert e._matches("irrelevant", None, HookEvent.USER_PROMPT_SUBMIT)

    def test_invalid_regex_returns_false(self):
        e = self._engine()
        assert not e._matches("[invalid", "test", HookEvent.PRE_TOOL_USE)

    def test_no_value_with_pattern_no_match(self):
        e = self._engine()
        assert not e._matches("shell", None, HookEvent.PRE_TOOL_USE)


# =====================================================================
# HookEngine JSON parsing tests
# =====================================================================


class TestHookEngineJsonParsing:
    def _engine(self) -> HookEngine:
        return HookEngine(session_id="test", cwd="/tmp")

    def test_parse_universal_fields(self):
        e = self._engine()
        r = HookResult()
        e._parse_json_output(
            r,
            json.dumps(
                {
                    "continue": False,
                    "stopReason": "build failed",
                    "suppressOutput": True,
                    "systemMessage": "warning: unstable",
                    "additionalContext": "extra info",
                }
            ),
        )
        assert r.continue_ is False
        assert r.stop_reason == "build failed"
        assert r.suppress_output is True
        assert r.system_message == "warning: unstable"
        assert r.additional_context == "extra info"

    def test_parse_decision(self):
        e = self._engine()
        r = HookResult()
        e._parse_json_output(
            r,
            json.dumps(
                {
                    "decision": "block",
                    "reason": "tests failing",
                }
            ),
        )
        assert r.decision == "block"
        assert r.reason == "tests failing"

    def test_parse_hook_specific_output(self):
        e = self._engine()
        r = HookResult()
        e._parse_json_output(
            r,
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "rm -rf blocked",
                    },
                }
            ),
        )
        assert r.hook_specific_output is not None
        assert r.permission_decision == "deny"
        assert r.permission_decision_reason == "rm -rf blocked"

    def test_parse_invalid_json(self):
        e = self._engine()
        r = HookResult()
        e._parse_json_output(r, "not json at all")
        # Should not crash, fields remain default
        assert r.continue_ is True
        assert r.decision is None

    def test_parse_non_dict_json(self):
        e = self._engine()
        r = HookResult()
        e._parse_json_output(r, json.dumps([1, 2, 3]))
        assert r.continue_ is True


# =====================================================================
# HookEngine execution tests (integration)
# =====================================================================


class TestHookEngineExecution:
    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            yield d

    def _create_script(self, tmp_dir: str, name: str, content: str) -> str:
        """Create a Python script in tmp_dir and return its path."""
        script_path = os.path.join(tmp_dir, name)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(content)
        return script_path

    @pytest.mark.asyncio
    async def test_fire_no_hooks(self):
        engine = HookEngine(disabled=True)
        result = await engine.fire(HookEvent.PRE_TOOL_USE, match_value="shell")
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_fire_no_matching_event(self):
        engine = HookEngine(
            hooks_config={
                "PostToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command="echo ok"),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=os.getcwd(),
        )
        # Fire PreToolUse — no hooks registered for it
        result = await engine.fire(HookEvent.PRE_TOOL_USE, match_value="shell")
        assert len(result.results) == 0

    @pytest.mark.asyncio
    async def test_fire_exit_0_success(self, tmp_dir):
        """Hook that exits 0 with no output = allow."""
        script = self._create_script(
            tmp_dir,
            "ok.py",
            textwrap.dedent(
                """\
            import sys
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell", "tool_input": {"command": "ls"}},
        )
        assert len(result.results) == 1
        assert result.results[0].is_success
        assert not result.any_deny

    @pytest.mark.asyncio
    async def test_fire_exit_2_blocks(self, tmp_dir):
        """Hook that exits 2 = blocking error."""
        script = self._create_script(
            tmp_dir,
            "block.py",
            textwrap.dedent(
                """\
            import sys
            print("Blocked: dangerous command", file=sys.stderr)
            sys.exit(2)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell", "tool_input": {"command": "rm -rf /"}},
        )
        assert result.any_blocking
        assert result.should_block
        assert "dangerous command" in result.blocking_stderr

    @pytest.mark.asyncio
    async def test_fire_json_deny(self, tmp_dir):
        """Hook that outputs JSON deny decision."""
        script = self._create_script(
            tmp_dir,
            "deny.py",
            textwrap.dedent(
                """\
            import json, sys
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "rm commands not allowed",
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell", "tool_input": {"command": "rm -rf /tmp"}},
        )
        assert result.any_deny
        assert result.blocking_reason == "rm commands not allowed"

    @pytest.mark.asyncio
    async def test_fire_json_allow(self, tmp_dir):
        """Hook that outputs JSON allow decision (bypasses approval)."""
        script = self._create_script(
            tmp_dir,
            "allow.py",
            textwrap.dedent(
                """\
            import json, sys
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "auto-approved by hook",
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell", "tool_input": {"command": "npm test"}},
        )
        assert result.any_allow
        assert not result.any_deny

    @pytest.mark.asyncio
    async def test_fire_json_updated_input(self, tmp_dir):
        """Hook that modifies tool input."""
        script = self._create_script(
            tmp_dir,
            "modify.py",
            textwrap.dedent(
                """\
            import json, sys
            result = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "updatedInput": {"command": "npm run lint -- --fix"},
                }
            }
            json.dump(result, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell", "tool_input": {"command": "npm run lint"}},
        )
        assert result.first_updated_input == {"command": "npm run lint -- --fix"}

    @pytest.mark.asyncio
    async def test_fire_stop_hook_block(self, tmp_dir):
        """Stop hook that forces agent to continue."""
        script = self._create_script(
            tmp_dir,
            "stop.py",
            textwrap.dedent(
                """\
            import json, sys
            result = {
                "decision": "block",
                "reason": "Tests not passing yet, keep working",
            }
            json.dump(result, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "Stop": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ]
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(HookEvent.STOP, extra={"stop_hook_active": False})
        assert result.should_block
        assert result.blocking_reason == "Tests not passing yet, keep working"

    @pytest.mark.asyncio
    async def test_fire_continue_false_stops(self, tmp_dir):
        """Hook that sets continue=false to halt the agent."""
        script = self._create_script(
            tmp_dir,
            "halt.py",
            textwrap.dedent(
                """\
            import json, sys
            result = {
                "continue": False,
                "stopReason": "Build failed, fix errors before continuing",
            }
            json.dump(result, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PostToolUse": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ]
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.POST_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell"},
        )
        assert result.should_stop
        assert result.stop_reason == "Build failed, fix errors before continuing"

    @pytest.mark.asyncio
    async def test_fire_timeout(self, tmp_dir):
        """Hook that times out."""
        script = self._create_script(
            tmp_dir,
            "slow.py",
            textwrap.dedent(
                """\
            import time
            time.sleep(30)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(
                                type="command",
                                command=f'"{sys.executable}" "{script}"',
                                timeout=1,
                            ),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell"},
        )
        assert len(result.results) == 1
        assert result.results[0].timed_out

    @pytest.mark.asyncio
    async def test_fire_reads_stdin_json(self, tmp_dir):
        """Hook can read JSON from stdin."""
        script = self._create_script(
            tmp_dir,
            "echo_stdin.py",
            textwrap.dedent(
                """\
            import json, sys
            data = json.load(sys.stdin)
            tool_name = data.get("tool_name", "unknown")
            result = {"additionalContext": f"Processed tool: {tool_name}"}
            json.dump(result, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PostToolUse": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ]
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.POST_TOOL_USE,
            match_value="write_file",
            extra={"tool_name": "write_file", "tool_input": {"path": "/tmp/test.txt"}},
        )
        ctx = result.collect_additional_context()
        assert ctx == "Processed tool: write_file"

    @pytest.mark.asyncio
    async def test_matcher_filtering(self, tmp_dir):
        """Only matching handlers fire."""
        script = self._create_script(
            tmp_dir,
            "marker.py",
            textwrap.dedent(
                """\
            import json, sys
            json.dump({"additionalContext": "fired"}, sys.stdout)
            sys.exit(0)
        """
            ),
        )

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                    HookMatcher(
                        matcher="write_file",
                        hooks=[
                            HookHandler(type="command", command=f'"{sys.executable}" "{script}"'),
                        ],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        # Fire for "shell" — should match first matcher only
        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell"},
        )
        assert len(result.results) == 1

        # Fire for "read_file" — should match neither
        result2 = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="read_file",
            extra={"tool_name": "read_file"},
        )
        assert len(result2.results) == 0

    @pytest.mark.asyncio
    async def test_deduplication(self, tmp_dir):
        """Identical handlers are deduplicated."""
        script = self._create_script(
            tmp_dir,
            "dup.py",
            textwrap.dedent(
                """\
            import sys
            sys.exit(0)
        """
            ),
        )
        cmd = f'"{sys.executable}" "{script}"'

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=cmd),
                        ]
                    ),
                    # Second matcher that also matches (no pattern = match all)
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=cmd),  # same command
                        ]
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell"},
        )
        # Should only run once due to deduplication
        assert len(result.results) == 1

    def test_get_registrations(self, tmp_dir):
        """Engine exposes normalized registrations for UI/debug consumers."""
        script = self._create_script(
            tmp_dir,
            "noop.py",
            "import sys\nsys.exit(0)\n",
        )
        cmd = f'"{sys.executable}" "{script}"'
        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        matcher="shell",
                        hooks=[HookHandler(type="command", command=cmd)],
                    ),
                ],
                "Notification": [
                    HookMatcher(
                        hooks=[HookHandler(type="command", command=cmd)],
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        all_rows = engine.get_registrations()
        assert len(all_rows) == 2
        pre_tool_rows = engine.get_registrations(HookEvent.PRE_TOOL_USE)
        assert len(pre_tool_rows) == 1
        assert pre_tool_rows[0].matcher == "shell"
        assert pre_tool_rows[0].event == HookEvent.PRE_TOOL_USE

    @pytest.mark.asyncio
    async def test_deduplication_keeps_distinct_timeout(self, tmp_dir):
        """Handlers that differ in runtime options are not deduplicated."""
        script = self._create_script(
            tmp_dir,
            "dup_timeout.py",
            "import sys\nsys.exit(0)\n",
        )
        cmd = f'"{sys.executable}" "{script}"'

        engine = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=cmd, timeout=30),
                        ]
                    ),
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command=cmd, timeout=45),
                        ]
                    ),
                ],
            },
            session_id="test",
            cwd=tmp_dir,
        )

        result = await engine.fire(
            HookEvent.PRE_TOOL_USE,
            match_value="shell",
            extra={"tool_name": "shell"},
        )
        assert len(result.results) == 2


# =====================================================================
# HooksConfig tests
# =====================================================================


class TestHooksConfig:
    def test_to_engine_dict_empty(self):
        from config.config import HooksConfig

        cfg = HooksConfig()
        d = cfg.to_engine_dict()
        assert d == {}

    def test_to_engine_dict_with_hooks(self):
        from config.config import HookHandlerConfig, HookMatcherConfig, HooksConfig

        cfg = HooksConfig(
            PreToolUse=[
                HookMatcherConfig(
                    matcher="shell",
                    hooks=[HookHandlerConfig(type="command", command="echo ok")],
                ),
            ],
        )
        d = cfg.to_engine_dict()
        assert "PreToolUse" in d
        assert len(d["PreToolUse"]) == 1
        assert d["PreToolUse"][0].matcher == "shell"
        assert len(d["PreToolUse"][0].hooks) == 1
        assert d["PreToolUse"][0].hooks[0].command == "echo ok"

    def test_disabled_config(self):
        from config.config import HooksConfig

        cfg = HooksConfig(disabled=True)
        assert cfg.disabled


# =====================================================================
# Engine property tests
# =====================================================================


class TestHookEngineProperties:
    def test_has_hooks_disabled(self):
        e = HookEngine(disabled=True)
        assert not e.has_hooks

    def test_has_hooks_empty(self):
        e = HookEngine()
        assert not e.has_hooks

    def test_has_hooks_with_hooks(self):
        e = HookEngine(
            hooks_config={
                "PreToolUse": [HookMatcher(hooks=[HookHandler(type="command", command="echo")])],
            },
        )
        assert e.has_hooks

    def test_get_registered_events(self):
        e = HookEngine(
            hooks_config={
                "PreToolUse": [HookMatcher(hooks=[HookHandler(type="command", command="echo")])],
                "Stop": [HookMatcher(hooks=[HookHandler(type="command", command="echo")])],
            },
        )
        events = e.get_registered_events()
        assert HookEvent.PRE_TOOL_USE in events
        assert HookEvent.STOP in events
        assert HookEvent.POST_TOOL_USE not in events

    def test_get_hook_count(self):
        e = HookEngine(
            hooks_config={
                "PreToolUse": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command="echo 1"),
                            HookHandler(type="command", command="echo 2"),
                        ]
                    ),
                ],
                "Stop": [
                    HookMatcher(
                        hooks=[
                            HookHandler(type="command", command="echo 3"),
                        ]
                    ),
                ],
            },
        )
        assert e.get_hook_count() == 3
        assert e.get_hook_count(HookEvent.PRE_TOOL_USE) == 2
        assert e.get_hook_count(HookEvent.STOP) == 1
        assert e.get_hook_count(HookEvent.POST_TOOL_USE) == 0

    def test_env_vars(self):
        e = HookEngine(
            session_id="abc123",
            cwd="/my/project",
        )
        env = e._get_hook_env()
        assert env["PICHU_PROJECT_DIR"] == "/my/project"
        assert env["PICHU_SESSION_ID"] == "abc123"
        assert env["PICHU_HOOK"] == "1"
        assert env["PICHU_PROJECT_DIR"] == "/my/project"

    def test_expand_env_vars(self):
        e = HookEngine(cwd="/my/project")
        assert e._expand_env_vars("$PICHU_PROJECT_DIR/hooks/test.sh") == "/my/project/hooks/test.sh"
        assert e._expand_env_vars("${PICHU_PROJECT_DIR}/hooks/test.sh") == "/my/project/hooks/test.sh"


# =====================================================================
# New features: transcript_path, stop_hook_active, SubagentStop,
#               suppressOutput, enhanced tool_response
# =====================================================================


class TestTranscriptPath:
    def test_transcript_path_in_input(self):
        """HookInput now includes transcript_path."""
        inp = HookInput(
            session_id="abc",
            transcript_path="/path/to/transcript.jsonl",
            cwd="/tmp",
            hook_event_name="PreToolUse",
        )
        d = inp.to_json_dict()
        assert d["transcript_path"] == "/path/to/transcript.jsonl"

    def test_transcript_path_empty_by_default(self):
        inp = HookInput(session_id="x", cwd="/")
        d = inp.to_json_dict()
        assert d["transcript_path"] == ""

    def test_engine_transcript_path(self):
        """Engine passes transcript_path to hook input."""
        e = HookEngine(
            session_id="test",
            cwd="/tmp",
            transcript_path="/my/transcript.jsonl",
        )
        assert e.transcript_path == "/my/transcript.jsonl"

    def test_engine_transcript_path_setter(self):
        e = HookEngine(session_id="test", cwd="/tmp")
        assert e.transcript_path == ""
        e.transcript_path = "/new/path.jsonl"
        assert e.transcript_path == "/new/path.jsonl"


class TestStopHookActive:
    def test_stop_hook_active_default_false(self):
        e = HookEngine(session_id="test", cwd="/tmp")
        assert e.stop_hook_active is False

    def test_stop_hook_active_setter(self):
        e = HookEngine(session_id="test", cwd="/tmp")
        e.stop_hook_active = True
        assert e.stop_hook_active is True

    @pytest.mark.asyncio
    async def test_stop_hook_injects_stop_hook_active(self):
        """Stop event input should include stop_hook_active field."""
        import tempfile

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            # Create a script that echoes stdin back
            script_path = os.path.join(tmp_dir, "echo_stdin.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(
                    textwrap.dedent(
                        """\
                    import json, sys
                    data = json.load(sys.stdin)
                    # Write the stop_hook_active value to a file
                    with open(data["cwd"] + "/stop_active.txt", "w") as out:
                        out.write(str(data.get("stop_hook_active", "MISSING")))
                    sys.exit(0)
                """
                    )
                )

            engine = HookEngine(
                hooks_config={
                    "Stop": [
                        HookMatcher(
                            hooks=[
                                HookHandler(
                                    type="command",
                                    command=f'"{sys.executable}" "{script_path}"',
                                ),
                            ]
                        ),
                    ],
                },
                session_id="test",
                cwd=tmp_dir,
            )

            # First fire: stop_hook_active should be False
            await engine.fire(HookEvent.STOP)
            result_file = os.path.join(tmp_dir, "stop_active.txt")
            assert os.path.exists(result_file)
            with open(result_file) as f:
                assert f.read() == "False"

            # Set it to True and fire again
            engine.stop_hook_active = True
            await engine.fire(HookEvent.STOP)
            with open(result_file) as f:
                assert f.read() == "True"


class TestSubagentStopEvent:
    def test_subagent_stop_in_enum(self):
        assert HookEvent.SUBAGENT_STOP.value == "SubagentStop"

    def test_subagent_stop_is_blocking(self):
        assert HookEvent.SUBAGENT_STOP in _BLOCKING_EVENTS

    @pytest.mark.asyncio
    async def test_fire_subagent_stop(self):
        """SubagentStop event fires and can block."""
        import tempfile

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            script_path = os.path.join(tmp_dir, "subagent_stop.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(
                    textwrap.dedent(
                        """\
                    import json, sys
                    data = json.load(sys.stdin)
                    result = {"decision": "block", "reason": "sub-agent needs more work"}
                    json.dump(result, sys.stdout)
                    sys.exit(0)
                """
                    )
                )

            engine = HookEngine(
                hooks_config={
                    "SubagentStop": [
                        HookMatcher(
                            hooks=[
                                HookHandler(
                                    type="command",
                                    command=f'"{sys.executable}" "{script_path}"',
                                ),
                            ]
                        ),
                    ],
                },
                session_id="test",
                cwd=tmp_dir,
            )

            result = await engine.fire(
                HookEvent.SUBAGENT_STOP,
                extra={
                    "tool_name": "subagent_architect",
                    "subagent_name": "architect",
                    "success": True,
                    "output": "Done reviewing",
                },
            )
            assert result.should_block
            assert result.blocking_reason == "sub-agent needs more work"

    def test_subagent_stop_in_config(self):
        """HooksConfig has SubagentStop field."""
        from config.config import HookHandlerConfig, HookMatcherConfig, HooksConfig

        cfg = HooksConfig(
            SubagentStop=[
                HookMatcherConfig(
                    hooks=[HookHandlerConfig(type="command", command="echo done")],
                ),
            ],
        )
        d = cfg.to_engine_dict()
        assert "SubagentStop" in d


class TestSuppressOutput:
    def test_any_suppress_output_false(self):
        fr = HookFireResult(
            event=HookEvent.POST_TOOL_USE,
            results=[
                HookResult(exit_code=0),
            ],
        )
        assert not fr.any_suppress_output

    def test_any_suppress_output_true(self):
        fr = HookFireResult(
            event=HookEvent.POST_TOOL_USE,
            results=[
                HookResult(exit_code=0, suppress_output=True),
            ],
        )
        assert fr.any_suppress_output

    def test_any_suppress_output_mixed(self):
        fr = HookFireResult(
            event=HookEvent.POST_TOOL_USE,
            results=[
                HookResult(exit_code=0, suppress_output=False),
                HookResult(exit_code=0, suppress_output=True),
            ],
        )
        assert fr.any_suppress_output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

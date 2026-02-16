"""Loop detection system for the agentic loop.

Detects repetitive agent behavior using multiple strategies:
1. Repeated Tool Calls — same tool + identical args N times in a window
2. Repeated Errors — same error message recurring N consecutive times
3. Response Similarity — near-identical LLM text across turns
4. Oscillation Detection — A→B→A→B flip-flop patterns
5. Progress Stall — no new unique actions over N turns

Inspired by practical agent runtime patterns (stop_hook_active,
iteration limits, external quality gates).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum

logger = logging.getLogger(__name__)

# Data types


class LoopStrategy(str, Enum):
    """Which detection strategy flagged a loop."""

    REPEATED_TOOL_CALLS = "repeated_tool_calls"
    REPEATED_ERRORS = "repeated_errors"
    RESPONSE_SIMILARITY = "response_similarity"
    OSCILLATION = "oscillation"
    PROGRESS_STALL = "progress_stall"


class LoopAction(str, Enum):
    """What action to take when a loop is detected."""

    WARN_AND_NUDGE = "warn_and_nudge"
    PAUSE = "pause"
    STOP = "stop"


@dataclass
class TurnRecord:
    """Snapshot of a single agentic turn for loop analysis."""

    turn_number: int
    tool_calls: list[tuple[str, str]] = field(default_factory=list)  # (name, args_hash)
    response_text: str = ""
    response_hash: str = ""
    errors: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class LoopDetectionResult:
    """Result returned when a loop is detected."""

    strategy: LoopStrategy
    description: str
    turn_count: int  # how many turns triggered detection
    action: LoopAction
    details: dict = field(default_factory=dict)

    @property
    def nudge_message(self) -> str:
        """Construct a context-injection message for the agent."""
        return (
            f"[Loop detected — {self.strategy.value}]: {self.description} "
            f"Try a fundamentally different approach instead of repeating "
            f"the same actions."
        )


# Configuration


@dataclass
class LoopDetectionConfig:
    """Configuration for loop detection thresholds."""

    enabled: bool = True
    max_repeated_tool_calls: int = 3
    max_repeated_errors: int = 3
    max_similar_responses: int = 3
    similarity_threshold: float = 0.85
    window_size: int = 10
    action: str = "warn_and_nudge"  # "warn_and_nudge" | "pause" | "stop"

    @property
    def action_enum(self) -> LoopAction:
        try:
            return LoopAction(self.action)
        except ValueError:
            return LoopAction.WARN_AND_NUDGE


# Helpers


def _hash_args(args: dict) -> str:
    """Deterministic hash of tool arguments."""
    import json

    raw = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _hash_text(text: str) -> str:
    """Short hash of response text."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _text_similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two strings (0.0–1.0).

    For performance, compares first 2000 chars max.
    """
    a_trunc = a[:2000]
    b_trunc = b[:2000]
    if not a_trunc or not b_trunc:
        return 0.0
    return SequenceMatcher(None, a_trunc, b_trunc).ratio()


def _normalize_error(error: str) -> str:
    """Normalize an error string for comparison (strip whitespace, lowercase)."""
    return " ".join(error.lower().split())


# LoopDetector


class LoopDetector:
    """Tracks agentic turns and detects repetitive behavior.

    Usage::

        detector = LoopDetector(config)
        # After each turn:
        detector.record_turn(turn_record)
        result = detector.check()
        if result:
            # handle loop
    """

    def __init__(self, config: LoopDetectionConfig | None = None) -> None:
        self.config = config or LoopDetectionConfig()
        # Keep 2x window so progress-stall can compare recent vs earlier
        self._history: deque[TurnRecord] = deque(maxlen=max(self.config.window_size * 2, 20))
        # Track unique actions ever seen (for progress-stall detection)
        self._unique_actions: set[str] = set()
        # Track stall window — unique actions seen in last N turns
        self._suppressed: bool = False  # suppress after first detection until reset

    # Public API

    def record_turn(self, record: TurnRecord) -> None:
        """Record a completed turn for analysis."""
        self._history.append(record)
        # Update unique action fingerprints
        for name, args_hash in record.tool_calls:
            self._unique_actions.add(f"{name}:{args_hash}")

    def check(self) -> LoopDetectionResult | None:
        """Run all detection strategies.  Returns the first match or None."""
        if not self.config.enabled or self._suppressed:
            return None
        if len(self._history) < 2:
            return None

        # Run strategies in priority order
        for strategy_fn in (
            self._check_repeated_tool_calls,
            self._check_repeated_errors,
            self._check_response_similarity,
            self._check_oscillation,
            self._check_progress_stall,
        ):
            result = strategy_fn()
            if result:
                self._suppressed = True  # suppress until reset
                logger.warning("Loop detected: %s — %s", result.strategy.value, result.description)
                return result

        return None

    def reset(self) -> None:
        """Reset detector state (e.g., after user intervention)."""
        self._history.clear()
        self._unique_actions.clear()
        self._suppressed = False

    def unsuppress(self) -> None:
        """Re-enable detection after a nudge (e.g., after 2 more turns)."""
        self._suppressed = False

    @property
    def history_len(self) -> int:
        return len(self._history)

    # Strategy 1: Repeated Tool Calls

    def _check_repeated_tool_calls(self) -> LoopDetectionResult | None:
        """Detect same tool+args called N+ times within the window."""
        threshold = self.config.max_repeated_tool_calls
        window = self.config.window_size
        counter: Counter[str] = Counter()

        recent_history = list(self._history)[-window:]
        for record in recent_history:
            for name, args_hash in record.tool_calls:
                key = f"{name}:{args_hash}"
                counter[key] += 1

        for key, count in counter.most_common(1):
            if count >= threshold:
                tool_name = key.split(":")[0]
                return LoopDetectionResult(
                    strategy=LoopStrategy.REPEATED_TOOL_CALLS,
                    description=(
                        f"Tool '{tool_name}' called with identical arguments "
                        f"{count} times in the last {len(recent_history)} turns."
                    ),
                    turn_count=count,
                    action=self.config.action_enum,
                    details={"tool_key": key, "count": count},
                )
        return None

    # Strategy 2: Repeated Errors

    def _check_repeated_errors(self) -> LoopDetectionResult | None:
        """Detect the same error occurring N+ consecutive times."""
        threshold = self.config.max_repeated_errors
        if threshold < 2:
            return None

        # Collect all errors in order from recent history
        recent_errors: list[str] = []
        for record in self._history:
            for err in record.errors:
                recent_errors.append(_normalize_error(err))

        if len(recent_errors) < threshold:
            return None

        # Check trailing N errors for sameness
        tail = recent_errors[-threshold:]
        if len(set(tail)) == 1:
            return LoopDetectionResult(
                strategy=LoopStrategy.REPEATED_ERRORS,
                description=(f"The same error occurred {threshold} consecutive times: '{tail[0][:120]}...'"),
                turn_count=threshold,
                action=self.config.action_enum,
                details={"error": tail[0], "count": threshold},
            )
        return None

    # Strategy 3: Response Similarity

    def _check_response_similarity(self) -> LoopDetectionResult | None:
        """Detect near-identical LLM responses across consecutive turns."""
        threshold = self.config.max_similar_responses
        sim_threshold = self.config.similarity_threshold

        # Need at least `threshold` records with text
        recent = [r for r in self._history if r.response_text.strip()]
        if len(recent) < threshold:
            return None

        tail = list(recent)[-threshold:]
        # Compare all pairs in the tail — all must be similar
        for i in range(len(tail)):
            for j in range(i + 1, len(tail)):
                sim = _text_similarity(tail[i].response_text, tail[j].response_text)
                if sim < sim_threshold:
                    return None

        return LoopDetectionResult(
            strategy=LoopStrategy.RESPONSE_SIMILARITY,
            description=(
                f"The last {threshold} LLM responses are nearly identical (similarity ≥ {sim_threshold:.0%})."
            ),
            turn_count=threshold,
            action=self.config.action_enum,
            details={"threshold": sim_threshold, "count": threshold},
        )

    # Strategy 4: Oscillation Detection

    def _check_oscillation(self) -> LoopDetectionResult | None:
        """Detect A→B→A→B flip-flop patterns in tool call sequences."""
        # Need at least 4 turns
        if len(self._history) < 4:
            return None

        # Build a fingerprint per turn from tool calls
        fingerprints: list[str] = []
        for record in self._history:
            if record.tool_calls:
                fp = "|".join(f"{n}:{h}" for n, h in sorted(record.tool_calls))
            else:
                fp = "<no_tools>"
            fingerprints.append(fp)

        # Check trailing 4+ items for A-B-A-B pattern
        tail = fingerprints[-4:]
        if tail[0] == tail[2] and tail[1] == tail[3] and tail[0] != tail[1]:
            # Confirmed oscillation — check how far back it extends
            pattern_len = 4
            idx = len(fingerprints) - 5
            while idx >= 0 and fingerprints[idx] == fingerprints[idx + 2]:
                pattern_len += 1
                idx -= 1

            tool_a = tail[0].split(":")[0].split("|")[0]
            tool_b = tail[1].split(":")[0].split("|")[0]
            return LoopDetectionResult(
                strategy=LoopStrategy.OSCILLATION,
                description=(f"Detected oscillation between '{tool_a}' and '{tool_b}' over {pattern_len} turns."),
                turn_count=pattern_len,
                action=self.config.action_enum,
                details={
                    "pattern_a": tail[0],
                    "pattern_b": tail[1],
                    "length": pattern_len,
                },
            )
        return None

    # Strategy 5: Progress Stall

    def _check_progress_stall(self) -> LoopDetectionResult | None:
        """Detect no new unique actions over the last N turns.

        Triggers when the entire window consists of at most 2 distinct
        action fingerprints AND there is earlier history that was more
        diverse (i.e., the agent used to do different things and is now
        stuck repeating).
        """
        window = self.config.window_size
        if len(self._history) < window:
            return None

        history = list(self._history)
        recent = history[-window:]
        earlier = history[:-window]

        # Must have earlier history to compare against
        if not earlier:
            return None

        # Collect unique actions in the window
        window_actions: set[str] = set()
        for record in recent:
            for name, args_hash in record.tool_calls:
                window_actions.add(f"{name}:{args_hash}")

        # Collect unique actions in earlier history
        earlier_actions: set[str] = set()
        for record in earlier:
            for name, args_hash in record.tool_calls:
                earlier_actions.add(f"{name}:{args_hash}")

        # Stall = window has very low diversity AND earlier was more diverse
        if len(window_actions) <= 2 and len(earlier_actions) > len(window_actions):
            return LoopDetectionResult(
                strategy=LoopStrategy.PROGRESS_STALL,
                description=(
                    f"No new unique actions in the last {window} turns. "
                    f"The agent appears stuck repeating {len(window_actions)} "
                    f"action(s) (previously used {len(earlier_actions)} distinct actions)."
                ),
                turn_count=window,
                action=self.config.action_enum,
                details={
                    "window_size": window,
                    "unique_in_window": len(window_actions),
                    "unique_earlier": len(earlier_actions),
                },
            )
        return None

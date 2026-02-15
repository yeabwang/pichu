"""Persistent transcript storage for resumable sub-agent conversations."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SubAgentTranscript:
    """
    Manages conversation transcript for a single sub-agent execution.

    Each transcript is a JSONL file with entries like:
    - {"type": "system", "content": "...", "timestamp": "..."}
    - {"type": "user", "content": "...", "timestamp": "..."}
    - {"type": "assistant", "content": "...", "tool_calls": [...], "timestamp": "..."}
    - {"type": "tool", "tool_call_id": "...", "content": "...", "timestamp": "..."}
    - {"type": "system", "subtype": "compact_boundary", "compactMetadata": {...}}
    """

    DEFAULT_BASE_DIR = "~/.pichu/subagents"

    def __init__(
        self,
        agent_id: str,
        session_id: str,
        subagent_name: str,
        base_dir: str | Path | None = None,
    ):
        """
        Initialize a transcript for a sub-agent.

        Args:
            agent_id: Unique ID for this agent execution.
            session_id: Session ID this agent belongs to.
            subagent_name: Name of the sub-agent type.
            base_dir: Base directory for transcript storage.
        """
        self.agent_id = agent_id
        self.session_id = session_id
        self.subagent_name = subagent_name

        if base_dir is None:
            base_dir = os.environ.get(
                "PICHU_SUBAGENT_TRANSCRIPTS_DIR",
                os.path.expanduser(self.DEFAULT_BASE_DIR),
            )

        self.base_dir = Path(base_dir).expanduser().resolve()
        self._ensure_directory()

        self._messages: list[dict[str, Any]] = []
        self._recording = True

    def _ensure_directory(self) -> None:
        """Create the transcript directory if it doesn't exist."""
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

    @property
    def transcript_dir(self) -> Path:
        """Get the directory for this session's transcripts."""
        return self.base_dir / self.session_id

    @property
    def transcript_path(self) -> Path:
        """Get the path to this agent's transcript file."""
        return self.transcript_dir / f"agent-{self.agent_id}.jsonl"

    @property
    def is_recording(self) -> bool:
        """Check if recording is enabled."""
        return self._recording

    def disable_recording(self) -> None:
        """Disable recording (used during resume to avoid duplicates)."""
        self._recording = False
        logger.debug(f"Recording disabled for agent {self.agent_id}")

    def enable_recording(self) -> None:
        """Enable recording."""
        self._recording = True

    def append(self, message: dict[str, Any]) -> None:
        """
        Append a message to the transcript.

        Args:
            message: The message to append (role, content, etc.)
        """
        if not self._recording:
            return

        # Add timestamp
        entry = {
            **message,
            "timestamp": datetime.now().isoformat(),
        }

        self._messages.append(entry)

        # Append to file
        try:
            with open(self.transcript_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write transcript entry: {e}")

    def append_system(self, content: str, subtype: str | None = None) -> None:
        """Append a system message."""
        msg = {"type": "system", "content": content}
        if subtype:
            msg["subtype"] = subtype
        self.append(msg)

    def append_user(self, content: str) -> None:
        """Append a user message."""
        self.append({"type": "user", "content": content})

    def append_assistant(self, content: str | None, tool_calls: list[dict[str, Any]] | None = None) -> None:
        """Append an assistant message."""
        msg: dict[str, Any] = {"type": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.append(msg)

    def append_tool_result(self, tool_call_id: str, content: str) -> None:
        """Append a tool result."""
        self.append(
            {
                "type": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )

    def append_metadata(self, subtype: str, metadata: dict[str, Any]) -> None:
        """Append a metadata entry (e.g., compact_boundary)."""
        self.append(
            {
                "type": "system",
                "subtype": subtype,
                "metadata": metadata,
            }
        )

    def get_messages(self) -> list[dict[str, Any]]:
        """Get all messages in the transcript."""
        return list(self._messages)

    def load(self) -> list[dict[str, Any]]:
        """
        Load transcript from disk.

        Returns:
            List of messages from the transcript file.
        """
        if not self.transcript_path.exists():
            logger.debug(f"No transcript found at {self.transcript_path}")
            return []

        messages = []
        try:
            with open(self.transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning(f"Invalid JSON in transcript: {e}")
        except Exception as e:
            logger.warning(f"Failed to load transcript: {e}")
            return []

        self._messages = messages
        logger.debug(f"Loaded {len(messages)} messages from transcript")
        return messages

    def to_conversation_messages(self) -> list[dict[str, Any]]:
        """
        Convert transcript to LLM conversation format.

        Returns:
            List of messages suitable for LLM API calls.
        """
        conversation = []

        for entry in self._messages:
            entry_type = entry.get("type")

            if entry_type == "system":
                # Only include actual system prompts, not metadata
                if entry.get("subtype") is None:
                    conversation.append(
                        {
                            "role": "system",
                            "content": entry.get("content", ""),
                        }
                    )

            elif entry_type == "user":
                conversation.append(
                    {
                        "role": "user",
                        "content": entry.get("content", ""),
                    }
                )

            elif entry_type == "assistant":
                msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": entry.get("content"),
                }
                if entry.get("tool_calls"):
                    msg["tool_calls"] = entry["tool_calls"]
                conversation.append(msg)

            elif entry_type == "tool":
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": entry.get("tool_call_id", ""),
                        "content": entry.get("content", ""),
                    }
                )

        return conversation

    def clear(self) -> None:
        """Clear the transcript (both in-memory and on disk)."""
        self._messages = []
        if self.transcript_path.exists():
            try:
                self.transcript_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete transcript: {e}")

    @classmethod
    def find_by_agent_id(
        cls,
        agent_id: str,
        base_dir: str | Path | None = None,
    ) -> SubAgentTranscript | None:
        """
        Find a transcript by agent ID across all sessions.

        Args:
            agent_id: The agent ID to search for.
            base_dir: Base directory to search in.

        Returns:
            SubAgentTranscript if found, None otherwise.
        """
        if base_dir is None:
            base_dir = os.environ.get(
                "PICHU_SUBAGENT_TRANSCRIPTS_DIR",
                os.path.expanduser(cls.DEFAULT_BASE_DIR),
            )

        base_path = Path(base_dir).expanduser().resolve()

        if not base_path.exists():
            return None

        # Search all session directories
        for session_dir in base_path.iterdir():
            if not session_dir.is_dir():
                continue

            transcript_file = session_dir / f"agent-{agent_id}.jsonl"
            if transcript_file.exists():
                # Extract info from first line
                try:
                    with open(transcript_file, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                        if first_line:
                            first_entry = json.loads(first_line)
                            subagent_name = first_entry.get("metadata", {}).get("subagent_name", "unknown")
                        else:
                            subagent_name = "unknown"
                except Exception:
                    subagent_name = "unknown"

                transcript = cls(
                    agent_id=agent_id,
                    session_id=session_dir.name,
                    subagent_name=subagent_name,
                    base_dir=base_dir,
                )
                transcript.load()
                return transcript

        return None

    @classmethod
    def list_all(
        cls,
        session_id: str | None = None,
        base_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """
        List all available transcripts.

        Args:
            session_id: Optional session ID to filter by.
            base_dir: Base directory to search in.

        Returns:
            List of transcript info dicts with agent_id, session_id, path.
        """
        if base_dir is None:
            base_dir = os.environ.get(
                "PICHU_SUBAGENT_TRANSCRIPTS_DIR",
                os.path.expanduser(cls.DEFAULT_BASE_DIR),
            )

        base_path = Path(base_dir).expanduser().resolve()

        if not base_path.exists():
            return []

        transcripts = []

        for session_dir in base_path.iterdir():
            if not session_dir.is_dir():
                continue

            if session_id and session_dir.name != session_id:
                continue

            for transcript_file in session_dir.glob("agent-*.jsonl"):
                # Extract agent_id from filename
                agent_id = transcript_file.stem.replace("agent-", "")
                transcripts.append(
                    {
                        "agent_id": agent_id,
                        "session_id": session_dir.name,
                        "path": str(transcript_file),
                        "size": transcript_file.stat().st_size,
                        "modified": datetime.fromtimestamp(transcript_file.stat().st_mtime).isoformat(),
                    }
                )

        return transcripts

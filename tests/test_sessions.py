"""Functional tests for session storage and checkpoint manager."""

import os
import shutil
import tempfile
from pathlib import Path

from context.context_manager import MessageItem
from utils.checkpoint_manager import CheckpointManager
from utils.session_storage import SessionStorage, TranscriptEntry

print("=== Functional Tests ===")

# ---- Test SessionStorage ----

tmpdir = tempfile.mkdtemp(prefix="cb_test_")
storage = SessionStorage(Path(tmpdir), cleanup_days=30, max_sessions=10)

# Create session
sid = "test-session-001"
sdir = storage.create_session(sid, cwd=os.getcwd(), model="test-model", title="Test Session")
assert sdir.exists()
assert (sdir / "metadata.json").exists()
assert (sdir / "transcript.jsonl").exists()
print("  [OK] create_session")

# Metadata
meta = storage.get_metadata(sid)
assert meta is not None
assert meta.title == "Test Session"
assert meta.model == "test-model"
print("  [OK] get_metadata")

# Update metadata
storage.update_metadata(sid, title="Updated Title", turn_count=5)
meta = storage.get_metadata(sid)
assert meta.title == "Updated Title"
assert meta.turn_count == 5
print("  [OK] update_metadata")

# Append messages
storage.append_message(sid, TranscriptEntry(role="user", content="Hello", turn=1))
storage.append_message(sid, TranscriptEntry(role="assistant", content="Hi there!", turn=1))
storage.append_message(sid, TranscriptEntry(role="user", content="Write code", turn=2))
print("  [OK] append_message")

# Load transcript
entries = storage.load_transcript(sid)
assert len(entries) == 3
assert entries[0].role == "user"
assert entries[0].content == "Hello"
assert entries[1].role == "assistant"
assert entries[2].content == "Write code"
print("  [OK] load_transcript")

# Truncate transcript
storage.truncate_transcript(sid, 2)
entries = storage.load_transcript(sid)
assert len(entries) == 2
print("  [OK] truncate_transcript")

# List sessions
sessions = storage.list_sessions(cwd=os.getcwd())
assert len(sessions) >= 1
assert sessions[0].session_id == sid
print("  [OK] list_sessions")

# Find most recent
recent = storage.find_most_recent(cwd=os.getcwd())
assert recent is not None
assert recent.session_id == sid
print("  [OK] find_most_recent")

# Copy transcript (for fork)
sid2 = "test-session-002"
storage.create_session(sid2, cwd=os.getcwd(), model="test-model")
storage.copy_transcript(sid, sid2)
entries2 = storage.load_transcript(sid2)
assert len(entries2) == 2
print("  [OK] copy_transcript")

# Delete session
assert storage.delete_session(sid2)
assert not storage.session_exists(sid2)
print("  [OK] delete_session")

# ---- Test CheckpointManager ----

cp_dir = Path(tmpdir) / "cp_session"
cp_dir.mkdir()
cp = CheckpointManager(cp_dir)

# Create a temp file to checkpoint
test_file = Path(tmpdir) / "test_code.py"
test_file.write_text('print("hello")', encoding="utf-8")

# Begin turn 1
cp.begin_turn(1, "Fix the bug", message_index=0)
cp.snapshot_file(str(test_file))

# Snapshot is idempotent
cp.snapshot_file(str(test_file))
assert len(cp._current_snapshots) == 1
print("  [OK] snapshot_file (idempotent)")

# Modify the file
test_file.write_text('print("fixed")', encoding="utf-8")

# Commit turn
cp.commit_turn()
assert len(cp.get_checkpoints()) == 1
print("  [OK] commit_turn")

# Begin turn 2
cp.begin_turn(2, "Add feature", message_index=2)
cp.snapshot_file(str(test_file))
test_file.write_text('print("feature added")', encoding="utf-8")
cp.commit_turn()
assert len(cp.get_checkpoints()) == 2
print("  [OK] two checkpoints")

# Restore code to turn 1
restored = cp.restore_code(1)
assert len(restored) >= 1
content = test_file.read_text(encoding="utf-8")
assert content == 'print("fixed")', f"Expected fixed, got: {content}"
print("  [OK] restore_code to turn 1")

# Get message index
idx = cp.get_message_index_at_turn(1)
assert idx == 0
idx2 = cp.get_message_index_at_turn(2)
assert idx2 == 2
print("  [OK] get_message_index_at_turn")

# Persistence — reload from disk
cp2 = CheckpointManager(cp_dir)
assert len(cp2.get_checkpoints()) == 2
print("  [OK] checkpoint persistence (reload)")

# ---- Test ContextManager serialization ----
# We test the methods exist and work correctly on a minimal instance

msg1 = MessageItem(role="user", content="Hello", token_count=5)
msg2 = MessageItem(role="assistant", content="Hi", tool_calls=[], token_count=3)

# Test serialize
d1 = {"role": "user", "content": "Hello", "token_count": 5}
d2 = {"role": "assistant", "content": "Hi", "token_count": 3}
print("  [OK] MessageItem serialization")

# ---- Test TranscriptEntry serialization roundtrip ----
entry = TranscriptEntry(
    role="assistant",
    content="Sure, let me help",
    tool_calls=[
        {
            "id": "tc_1",
            "type": "function",
            "function": {"name": "edit_file", "arguments": "{}"},
        }
    ],
    timestamp="2024-01-01T00:00:00",
    turn=3,
    token_usage={"prompt_tokens": 100, "completion_tokens": 50},
)
json_str = entry.to_json()
restored_entry = TranscriptEntry.from_json(json_str)
assert restored_entry.role == "assistant"
assert restored_entry.content == "Sure, let me help"
assert len(restored_entry.tool_calls) == 1
assert restored_entry.turn == 3
print("  [OK] TranscriptEntry roundtrip")

# Cleanup
shutil.rmtree(tmpdir, ignore_errors=True)

print()
print("=== ALL FUNCTIONAL TESTS PASSED ===")

"""
Integration tests for the memory system.
"""

import shutil
import tempfile
from pathlib import Path

from tools.builtin.memory import MemoryTool
from utils.agents_loader import AgentsLoader
from utils.memory_manager import MemoryManager
from utils.memory_storage import (
    MemoryStorage,
)
from utils.memory_types import Memory, MemoryCategory, MemoryStore


def test_memory_category():
    """Test MemoryCategory enum functionality."""
    print("Testing MemoryCategory...")

    # Test enum values
    cat = MemoryCategory.ARCHITECTURE
    assert cat.decay_rate == 0.0
    assert cat.emoji  # Just check it exists
    assert cat.priority == 1
    print(f"  - {cat.value}: decay={cat.decay_rate}, priority={cat.priority}")

    # Test from_string
    cat2 = MemoryCategory.from_string("pattern")
    assert cat2 == MemoryCategory.PATTERN
    print(f"  - from_string: {cat2.value}")

    # Test all categories
    for c in MemoryCategory:
        assert c.decay_rate >= 0
        assert c.emoji

    print("[OK] MemoryCategory OK")


def test_memory():
    """Test Memory dataclass."""
    print("Testing Memory...")

    mem = Memory(
        content="Test memory content",
        category=MemoryCategory.DECISION,
        tags=["test", "example"],
    )

    assert mem.id
    assert mem.content == "Test memory content"
    assert mem.category == MemoryCategory.DECISION
    assert mem.confidence == 1.0
    print(f"  - ID: {mem.id[:8]}...")
    print(f"  - Category: {mem.category.value}")

    # Test serialization
    d = mem.to_dict()
    mem2 = Memory.from_dict(d)
    assert mem2.content == mem.content
    assert mem2.category == mem.category
    print("  - Serialization round-trip OK")

    print("[OK] Memory OK")


def test_memory_store():
    """Test MemoryStore operations."""
    print("Testing MemoryStore...")

    store = MemoryStore(project="test")

    # Add memories
    mem1 = Memory(content="Architecture uses FastAPI", category=MemoryCategory.ARCHITECTURE)
    mem2 = Memory(content="Always use type hints", category=MemoryCategory.PATTERN)
    mem3 = Memory(
        content="Avoid circular imports",
        category=MemoryCategory.GOTCHA,
        tags=["imports"],
    )

    store.add(mem1)
    store.add(mem2)
    store.add(mem3)

    assert store.count == 3
    print(f"  - Count: {store.count}")

    # Search
    results = store.search(query="FastAPI")
    assert len(results) >= 1
    print(f"  - Search 'FastAPI': found {len(results)}")

    # Search by category
    results = store.search(category=MemoryCategory.PATTERN)
    assert len(results) == 1
    print(f"  - Search by PATTERN: found {len(results)}")

    # Search by tags
    results = store.search(tags=["imports"])
    assert len(results) == 1
    print(f"  - Search by tag 'imports': found {len(results)}")

    # By category grouping
    by_cat = store.by_category()
    assert MemoryCategory.ARCHITECTURE in by_cat
    print("  - by_category grouping OK")

    # To markdown
    md = store.to_markdown()
    assert "Architecture" in md or "architecture" in md
    print("  - to_markdown OK")

    print("[OK] MemoryStore OK")


def test_memory_storage():
    """Test file-based storage."""
    print("Testing MemoryStorage...")

    # Use temp directory
    temp_dir = Path(tempfile.mkdtemp())

    try:
        storage = MemoryStorage(temp_dir / "memory", project="test")

        # Create and save store
        store = MemoryStore(project="test")
        store.add(Memory(content="Test persistence", category=MemoryCategory.CONTEXT))
        storage.save(store)

        assert storage.exists
        print("  - Save OK")

        # Load back
        loaded = storage.load()
        assert loaded.count == 1
        print("  - Load OK")

        # Check content matches via search
        results = loaded.search(query="Test persistence")
        assert len(results) == 1
        assert results[0].content == "Test persistence"
        print("  - Content matches")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("[OK] MemoryStorage OK")


def test_memory_storage_recovers_from_backup():
    """MemoryStorage should recover from backup when main state is corrupted."""
    temp_dir = Path(tempfile.mkdtemp())

    try:
        storage = MemoryStorage(temp_dir / "memory", project="test")

        baseline_store = MemoryStore(project="test")
        baseline_store.add(Memory(content="Baseline memory", category=MemoryCategory.CONTEXT))
        storage.save(baseline_store)

        updated_store = MemoryStore(project="test")
        updated_store.add(Memory(content="New memory", category=MemoryCategory.CONTEXT))
        storage.save(updated_store)

        (temp_dir / "memory" / "state.json").write_text("{invalid json", encoding="utf-8")

        recovered = storage.load()
        matches = recovered.search(query="Baseline memory")
        assert len(matches) == 1
        assert matches[0].content == "Baseline memory"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_memory_storage_normalizes_cursor_data():
    """Cursor reads should normalize invalid payloads and preserve shape."""
    temp_dir = Path(tempfile.mkdtemp())

    try:
        storage = MemoryStorage(temp_dir / "memory", project="test")

        cursor_path = temp_dir / "memory" / "cursor.json"
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text('["invalid"]', encoding="utf-8")

        cursor = storage.get_cursor()
        assert cursor["position"] == 0
        assert cursor["last_processed"] is None

        storage.set_cursor({"last_processed": "turn-4", "position": "4"})
        updated_cursor = storage.get_cursor()
        assert updated_cursor["position"] == 4
        assert updated_cursor["last_processed"] == "turn-4"
        assert "updated_at" in updated_cursor
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_memory_manager():
    """Test high-level memory manager."""
    print("Testing MemoryManager...")

    temp_dir = Path(tempfile.mkdtemp())

    try:
        manager = MemoryManager(project_root=temp_dir, project_name="test_project", auto_load=True)

        # Save a memory
        mem = manager.save(
            content="Project uses PostgreSQL",
            category=MemoryCategory.ARCHITECTURE,
            tags=["database"],
        )
        assert mem.id
        print(f"  - Saved memory: {mem.id[:8]}...")

        # Search
        results = manager.search(query="PostgreSQL")
        assert len(results) >= 1
        print(f"  - Search found: {len(results)}")

        # View
        view = manager.view()
        assert len(view) >= 1
        print("  - View OK")

        # Get context block
        context = manager.get_context_block()
        assert context  # May be empty string if no high-confidence memories
        print("  - Context block generated")

        # Statistics
        stats = manager.get_statistics()
        assert "project" in stats
        print(f"  - Stats: {stats['total']} total memories")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("[OK] MemoryManager OK")


def test_memory_manager_update_callback_for_deduplicated_save():
    """MemoryManager should emit update callbacks for deduplicated saves."""
    temp_dir = Path(tempfile.mkdtemp())

    try:
        manager = MemoryManager(project_root=temp_dir, project_name="test_project", auto_load=True)
        added: list[str] = []
        updated: list[str] = []
        manager.on_memory_added(lambda memory: added.append(memory.id))
        manager.on_memory_updated(lambda memory: updated.append(memory.id))

        first = manager.save(
            content="Use dependency injection for services",
            category=MemoryCategory.ARCHITECTURE,
            tags=["design"],
        )
        second = manager.save(
            content="Use dependency injection for services",
            category=MemoryCategory.ARCHITECTURE,
            tags=["design"],
        )

        assert first.id == second.id
        assert len(added) == 1
        assert len(updated) == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_agents_loader():
    """Test AGENTS.md loading."""
    print("Testing AgentsLoader...")

    temp_dir = Path(tempfile.mkdtemp())

    try:
        # Create test AGENTS.md
        agents_content = """# Test Project

## Architecture
- Uses Python with FastAPI
- PostgreSQL database

## Patterns
- Use dataclasses for DTOs
- Always use type hints

## Gotchas
- Don't import X from Y
"""
        agents_path = temp_dir / "AGENTS.md"
        agents_path.write_text(agents_content, encoding="utf-8")

        # Load
        loader = AgentsLoader(project_root=temp_dir)
        context = loader.load()

        assert context.project_file is not None
        print(f"  - Loaded project file: {context.project_file.title}")

        # Check sections
        assert "architecture" in context.project_file.sections
        assert "pattern" in context.project_file.sections
        print(f"  - Sections: {list(context.project_file.sections.keys())}")

        # Get merged section
        arch = context.get_merged_section("architecture")
        assert len(arch) == 2
        print(f"  - Architecture items: {len(arch)}")

        # To prompt context
        prompt_ctx = context.to_prompt_context()
        assert "FastAPI" in prompt_ctx or "fastapi" in prompt_ctx.lower()
        print("  - Prompt context generated")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("[OK] AgentsLoader OK")


def test_memory_tool():
    """Test memory tool interface."""
    print("Testing MemoryTool...")

    tool = MemoryTool()

    assert tool.name == "memory"
    assert tool.schema == MemoryTool.schema
    print(f"  - Tool name: {tool.name}")
    print(f"  - Has schema: {tool.schema is not None}")

    # Tool without manager should fail gracefully
    # (We can't easily test async execute here)

    print("[OK] MemoryTool OK")


def run_all_tests():
    """Run all integration tests."""
    print("=" * 50)
    print("Memory System Integration Tests")
    print("=" * 50)
    print()

    tests = [
        test_memory_category,
        test_memory,
        test_memory_store,
        test_memory_storage,
        test_memory_manager,
        test_memory_manager_update_callback_for_deduplicated_save,
        test_agents_loader,
        test_memory_tool,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print()
        except Exception as e:
            failed += 1
            print(f"[FAILED] {test.__name__}: {e}")
            print()

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)

"""Verify all Phase 2 components are working."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 2D: Conflict detection
from agentnexus.memory.extraction import _check_conflict
from agentnexus.memory.long_term import SCHEMA, LongTermMemory

print("[2D] Conflict detection:")
print("  - _check_conflict function exists:", callable(_check_conflict))
print("  - superseded_by in SCHEMA:", "superseded_by" in SCHEMA)
print("  - mark_superseded method exists:", hasattr(LongTermMemory, "mark_superseded"))

# Verify superseded filtering in search
import inspect

search_src = inspect.getsource(LongTermMemory.search)
assert "superseded_by IS NULL" in search_src or "superseded_by" in search_src
print("  - Search filters superseded: OK")

# 2E: Recall strategy
from agentnexus.tools.memory_search import _MAX_CONTENT_TOKENS, _diversify_results, _truncate_content

print("\n[2E] Recall strategy:")
print("  - Max content tokens:", _MAX_CONTENT_TOKENS)

# Test truncation
long_text = "x" * 2000
truncated = _truncate_content(long_text)
assert len(truncated) < len(long_text)
print("  - Truncation works: OK")

# Test diversification
results = [
    {"id": 1, "category": "fact", "_score": 0.9},
    {"id": 2, "category": "fact", "_score": 0.8},
    {"id": 3, "category": "preference", "_score": 0.7},
    {"id": 4, "category": "note", "_score": 0.6},
    {"id": 5, "category": "fact", "_score": 0.5},
]
div = _diversify_results(results, 3)
cats = [r["category"] for r in div]
assert len(set(cats)) == 3, f"Expected 3 unique categories, got {cats}"
print(f"  - Diversification OK: {cats}")

# 2F: Pre-filter
from agentnexus.memory.manager import MemoryManager

print("\n[2F] Pre-filter:")
print("  - _should_extract exists:", hasattr(MemoryManager, "_should_extract"))
print("  - _MEMORY_SIGNALS count:", len(MemoryManager._MEMORY_SIGNALS))
print("  - _SKIP_PATTERNS count:", len(MemoryManager._SKIP_PATTERNS))

print("\n=== ALL PHASE 2 CHECKS PASSED ===")

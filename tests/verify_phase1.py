"""Verify all Phase 1 components are working."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. Categories merged
from agentnexus.memory.extraction import MEMORY_CATEGORIES, migrate_category
print("[1] Categories:", list(MEMORY_CATEGORIES.keys()))
assert list(MEMORY_CATEGORIES.keys()) == ["fact", "preference", "note"]

# 2. Migration works
assert migrate_category("entity_fact") == "fact"
assert migrate_category("user_preference") == "preference"
assert migrate_category("task_progress") == "note"
print("[2] Category migration OK")

# 3. memory_save accepts new categories
from agentnexus.tools.memory_save import _VALID_CATEGORIES, _CATEGORY_MIGRATION
assert "fact" in _VALID_CATEGORIES
assert "preference" in _VALID_CATEGORIES
assert "note" in _VALID_CATEGORIES
assert _CATEGORY_MIGRATION["entity_fact"] == "fact"
print("[3] memory_save categories OK")

# 4. TTL config
from agentnexus.memory.long_term import LongTermMemory
assert LongTermMemory._CATEGORY_TTL["fact"] is None
assert LongTermMemory._CATEGORY_TTL["preference"] is None
assert LongTermMemory._CATEGORY_TTL["note"] == 90
print("[4] TTL config OK")

# 5. Dynamic importance
row = {"importance": 0.7, "access_count": 10}
eff = LongTermMemory._effective_importance(row)
assert eff > 0.7, f"Expected >0.7, got {eff}"
print(f"[5] Dynamic importance OK: base=0.7, access=10 -> effective={eff:.3f}")

# 6. Prompt updated
from agentnexus.prompts import load_prompt
prompt = load_prompt("memory_extract")
assert "fact" in prompt
assert "preference" in prompt
assert "note" in prompt
print("[6] Extraction prompt OK")

# 7. memory_search uses new categories
from agentnexus.tools.memory_search import _is_entity_query
assert _is_entity_query("你知道我叫什么吗")
print("[7] memory_search OK")

print()
print("=== ALL PHASE 1 CHECKS PASSED ===")

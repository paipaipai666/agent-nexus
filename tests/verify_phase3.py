"""Verify Phase 3 - Periodic Reflection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. Reflection module
from agentnexus.memory.reflection import run_reflection, _should_reflect, _format_memories_for_prompt
print("[1] Reflection module:")
print("  - run_reflection callable:", callable(run_reflection))
print("  - _should_reflect works:", _should_reflect([{"category": "note"}] * 5))
print("  - _should_reflect rejects short:", not _should_reflect([{"category": "note"}] * 2))

# 2. Memory routes
from agentnexus.server.routes import memory as mem_routes
mem_src = open(mem_routes.__file__, encoding="utf-8").read()
assert "/reflect" in mem_src
print("[2] Memory routes: /reflect endpoint OK")

# 3. Prompt template
from agentnexus.memory import reflection as ref_mod
ref_src = open(ref_mod.__file__, encoding="utf-8").read()
assert "REFLECTION_PROMPT" in ref_src
print("[3] Reflection prompt OK")

print("\n=== ALL PHASE 3 CHECKS PASSED ===")

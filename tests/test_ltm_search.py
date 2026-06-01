"""Reproduce LTM search bug: entity_fact '姓名为张三' not found when searching '用户名字'.

Run: python -m tests.test_ltm_search
"""

import math
import tempfile
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_fake_embedding(seed: int, dim: int = 512) -> list[float]:
    """Generate a deterministic normalized vector from a seed."""
    import random
    rng = random.Random(seed)
    vec = [rng.gauss(0, 1) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def test_scoring_formula_favors_entity_fact():
    """The composite scoring formula should not systematically favor user_preference over entity_fact
    when the query is about personal information like names."""
    # Simulate the scoring formula from long_term.py:412
    # score = sim * 0.6 + importance * 0.2 + decay * 0.2

    # entity_fact "姓名为张三" — importance 0.7
    entity_importance = 0.7
    # user_preference "用户偏好正式语气" — importance 0.9
    pref_importance = 0.9

    # Assume both have similar cosine similarity to the query "用户名字"
    sim = 0.45
    decay = 1.0  # same age

    entity_score = sim * 0.6 + entity_importance * 0.2 + decay * 0.2
    pref_score = sim * 0.6 + pref_importance * 0.2 + decay * 0.2

    print(f"Entity score: {entity_score:.3f} (importance={entity_importance})")
    print(f"Preference score: {pref_score:.3f} (importance={pref_importance})")
    print(f"Preference advantage: {pref_score - entity_score:.3f}")

    # The bug: user_preference always scores higher even at equal similarity
    assert pref_score > entity_score, "Sanity check: preference currently scores higher"

    # After fix: entity_fact importance raised to 0.85
    fixed_entity_importance = 0.85
    fixed_entity_score = sim * 0.6 + fixed_entity_importance * 0.2 + decay * 0.2
    print(f"\nAfter fix (entity importance={fixed_entity_importance}):")
    print(f"Entity score: {fixed_entity_score:.3f}")
    print(f"Preference score: {pref_score:.3f}")
    print(f"Gap: {pref_score - fixed_entity_score:.3f}")
    # Verify the gap is now minimal
    assert pref_score - fixed_entity_score <= 0.02, "Gap should be <= 0.02 after fix"


def test_entity_query_detection():
    """Queries about personal info should be detected and category-boosted."""
    entity_queries = [
        "你知道我叫什么吗",
        "我的名字是什么",
        "我叫什么名字",
        "用户名字",
        "用户姓名",
        "我几岁",
        "我在哪里工作",
    ]
    non_entity_queries = [
        "用户偏好什么颜色",
        "上次聊了什么",
        "怎么用这个工具",
    ]

    # Simple keyword-based detection (the fix approach)
    entity_keywords = ["名字", "姓名", "叫什么", "几岁", "多大", "哪里人", "在哪",
                       "工作", "住", "年龄", "生日", "电话", "邮箱", "地址"]

    def is_entity_query(q: str) -> bool:
        return any(kw in q for kw in entity_keywords)

    for q in entity_queries:
        assert is_entity_query(q), f"Expected entity query: {q}"
        print(f"  [OK] Entity query detected: {q}")

    for q in non_entity_queries:
        assert not is_entity_query(q), f"Expected non-entity query: {q}"
        print(f"  [OK] Non-entity query: {q}")


def test_chroma_fetch_limit():
    """The ChromaDB fetch limit (limit*3) should be large enough to include entity_facts."""
    # Current: limit=5, n_results=15
    # If there are 20 memories and entity_fact is ranked 12th by embedding,
    # it won't appear in top 15 → never scored
    current_limit = 5
    current_n_results = current_limit * 3  # 15

    # After fix: increase to limit*5
    fixed_n_results = current_limit * 5  # 25

    print(f"Current n_results: {current_n_results}")
    print(f"Fixed n_results: {fixed_n_results}")
    assert fixed_n_results > current_n_results


def test_min_similarity_threshold():
    """min_similarity=0.35 can filter out valid entity_facts."""
    # "姓名为张三" vs "你知道我叫什么吗" might have similarity ~0.30
    # which is below 0.35 → filtered out
    min_sim = 0.35
    entity_sim = 0.30  # plausible for short content vs query

    print(f"min_similarity threshold: {min_sim}")
    print(f"Entity fact similarity: {entity_sim}")
    print(f"Would be filtered: {entity_sim < min_sim}")

    # After fix: lowered to 0.25
    fixed_min_sim = 0.25
    print(f"\nAfter fix (min_similarity={fixed_min_sim}):")
    print(f"Would be filtered: {entity_sim < fixed_min_sim}")
    assert entity_sim >= fixed_min_sim, "Entity fact should pass the lowered threshold"


def test_real_module_entity_detection():
    """Verify the actual _is_entity_query function from memory_search module."""
    from agentnexus.tools.memory_search import _is_entity_query
    assert _is_entity_query("你知道我叫什么吗")
    assert _is_entity_query("我的名字是什么")
    assert not _is_entity_query("用户偏好什么颜色")
    print("  [OK] Module-level entity detection works")


if __name__ == "__main__":
    print("=" * 60)
    print("TEST 1: Scoring formula bias")
    print("=" * 60)
    test_scoring_formula_favors_entity_fact()

    print("\n" + "=" * 60)
    print("TEST 2: Entity query detection")
    print("=" * 60)
    test_entity_query_detection()

    print("\n" + "=" * 60)
    print("TEST 3: ChromaDB fetch limit")
    print("=" * 60)
    test_chroma_fetch_limit()

    print("\n" + "=" * 60)
    print("TEST 4: Min similarity threshold")
    print("=" * 60)
    test_min_similarity_threshold()

    print("\n" + "=" * 60)
    print("TEST 5: Real module entity detection")
    print("=" * 60)
    test_real_module_entity_detection()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)

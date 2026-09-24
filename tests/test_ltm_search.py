"""LTM search entity-detection regression guard.

Run: python -m tests.test_ltm_search
"""


def test_real_module_entity_detection():
    """Verify the actual _is_entity_query function from memory_search module."""
    from agentnexus.tools.memory_search import _is_entity_query

    assert _is_entity_query("你知道我叫什么吗")
    assert _is_entity_query("我的名字是什么")
    assert not _is_entity_query("用户偏好什么颜色")


if __name__ == "__main__":
    test_real_module_entity_detection()
    print("ALL TESTS PASSED")

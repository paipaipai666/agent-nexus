"""Memory queries shared by terminal and HTTP adapters."""


def search_memories(store, query: str, limit: int = 5) -> list[dict]:
    """Query the selected memory backend without constructing another provider."""
    if not query.strip() or not 1 <= limit <= 100:
        raise ValueError("请输入查询文本，结果数量必须在 1 到 100 之间")
    return store.search_text(query, limit=limit)

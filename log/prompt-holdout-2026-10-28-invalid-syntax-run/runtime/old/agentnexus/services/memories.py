"""Memory queries shared by terminal and HTTP adapters."""


def search_memories(store, query: str, limit: int = 5) -> list[dict]:
    """Encode a text query for the store's existing vector-search contract."""
    from agentnexus.rag.embeddings import get_embedding_model

    if not query.strip() or not 1 <= limit <= 100:
        raise ValueError("请输入查询文本，结果数量必须在 1 到 100 之间")
    encoded = get_embedding_model().encode(query, normalize_embeddings=True)
    embedding = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
    return store.search(query_embedding=embedding, limit=limit)

"""Default memory query rewriting without eager model construction."""

_QUERY_REWRITE_PROMPT = """\
将以下用户查询改写为3-5个关键词或短语，用空格分隔，用于向量搜索。提取核心概念和人名/地名/专有名词，去除语气词和冗余描述。只输出关键词。

查询: {query}
关键词:"""


def rewrite_query(query: str) -> str:
    """Extract retrieval keywords and retain the original on model failure."""
    try:
        from agentnexus.core.llm import AgentLLM

        rewritten = AgentLLM().think(
            [{"role": "user", "content": _QUERY_REWRITE_PROMPT.format(query=query)}], silent=True)
        if rewritten and len(rewritten.strip()) >= 2:
            return rewritten.strip()
    except Exception:
        pass
    return query

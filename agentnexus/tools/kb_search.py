"""kb_search tool — search the structured knowledge base with citations."""

from agentnexus.rag.chroma_client import search as chroma_search
from agentnexus.rag.retriever import HybridRetriever, expand_queries


def kb_search(
    query: str,
    namespace: str = "default",
    top_k: int = 5,
    source: str = "",
    file_format: str = "",
    section_title: str = "",
    page_number: int | None = None,
) -> str:
    retriever = HybridRetriever(namespace=namespace)
    retriever.rebuild_from_catalog()
    if not retriever._chunks:
        return "[kb_search] 知识库为空"

    if retriever._reranker is None:
        retriever.load_reranker()

    where: dict[str, object] = {}
    if source:
        where["source_uri"] = source
    if file_format:
        where["format"] = file_format
    if section_title:
        where["section_title"] = section_title
    if page_number is not None:
        where["page_number"] = page_number

    dense_fused: dict[str, float] = {}
    for search_query in expand_queries(query):
        dense_results = chroma_search(
            search_query,
            limit=max(top_k * 2, 10),
            namespace=namespace,
            where=where or None,
        )
        for rank, item in enumerate(dense_results):
            dense_fused[item["id"]] = dense_fused.get(item["id"], 0.0) + 1.0 / (60 + rank + 1)
    dense = sorted(dense_fused.items(), key=lambda x: x[1], reverse=True)
    results = retriever.search(query, dense, top_k=top_k, min_score=0.0)
    if not results:
        return "[kb_search] 未找到相关知识"

    lines = [f"知识库检索结果 (namespace={namespace}):"]
    for item in results:
        metadata = item.metadata or {}
        source_uri = metadata.get("source_uri", "")
        section_title = metadata.get("section_title", "")
        suffix = f" [{section_title}]" if section_title else ""
        lines.append(
            f"- ({item.id}) {source_uri}{suffix} score={item.score:.2f}\n  {item.text}"
        )
    return "\n".join(lines)

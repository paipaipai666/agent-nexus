"""kb_search tool — search the structured knowledge base with citations."""

from agentnexus.rag.chroma_client import search as chroma_search
from agentnexus.rag.retriever import HybridRetriever


def kb_search(query: str, namespace: str = "default", top_k: int = 5) -> str:
    retriever = HybridRetriever(namespace=namespace)
    retriever.rebuild_from_catalog()
    if not retriever._chunks:
        return "[kb_search] 知识库为空"

    dense_results = chroma_search(query, limit=max(top_k * 2, 10), namespace=namespace)
    dense = [(item["id"], item["score"]) for item in dense_results]
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

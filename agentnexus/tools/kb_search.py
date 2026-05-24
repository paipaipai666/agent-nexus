"""kb_search tool — search the structured knowledge base with citations."""

from agentnexus.rag.chroma_client import search as chroma_search
from agentnexus.rag.retriever import HybridRetriever, expand_queries, result_citation, result_display_text


def _build_search_where(
    source: str = "",
    file_format: str = "",
    section_title: str = "",
    page_number: int | None = None,
    block_type: str = "",
    has_code: bool | None = None,
    has_list: bool | None = None,
    heading_depth: int | None = None,
) -> dict[str, object] | None:
    where: dict[str, object] = {}
    if source:
        where["source_uri"] = source
    if file_format:
        where["format"] = file_format
    if section_title:
        where["section_title"] = section_title
    if page_number is not None:
        where["page_number"] = page_number
    if block_type:
        where["block_type"] = block_type
    if has_code is not None:
        where["has_code"] = has_code
    if has_list is not None:
        where["has_list"] = has_list
    if heading_depth is not None:
        where["heading_depth"] = heading_depth
    return where or None


def kb_search(
    query: str,
    namespace: str = "default",
    top_k: int = 5,
    view: str = "section",
    source: str = "",
    file_format: str = "",
    section_title: str = "",
    page_number: int | None = None,
    block_type: str = "",
    has_code: bool | None = None,
    has_list: bool | None = None,
    heading_depth: int | None = None,
) -> str:
    retriever = HybridRetriever(namespace=namespace)
    retriever.rebuild_from_catalog()
    if not retriever._chunks:
        return "[kb_search] 知识库为空"

    if retriever._reranker is None:
        retriever.load_reranker()

    where = _build_search_where(
        source=source,
        file_format=file_format,
        section_title=section_title,
        page_number=page_number,
        block_type=block_type,
        has_code=has_code,
        has_list=has_list,
        heading_depth=heading_depth,
    )

    dense_fused: dict[str, float] = {}
    for search_query in expand_queries(query):
        dense_results = chroma_search(
            search_query,
            limit=max(top_k * 2, 10),
            namespace=namespace,
            where=where,
        )
        for rank, item in enumerate(dense_results):
            dense_fused[item["id"]] = dense_fused.get(item["id"], 0.0) + 1.0 / (60 + rank + 1)
    dense = sorted(dense_fused.items(), key=lambda x: x[1], reverse=True)
    results = retriever.search(
        query,
        dense,
        top_k=top_k,
        min_score=0.0,
        metadata_filters=where,
    )
    if not results:
        return "[kb_search] 未找到相关知识"
    results = retriever.expand_contexts(results, view=view)

    lines = [f"知识库检索结果 (namespace={namespace}):"]
    for item in results:
        lines.append(
            f"- {result_citation(item)} score={item.score:.2f}\n  {result_display_text(item)}"
        )
    return "\n".join(lines)

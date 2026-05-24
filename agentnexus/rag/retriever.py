import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING

import jieba
from rank_bm25 import BM25Okapi

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt

from .chroma_client import insert_documents, resolve_collection_name
from .chroma_client import search as chroma_search
from .ids import make_chunk_id, make_document_version, make_source_id
from .models import ChunkRecord, KnowledgeBaseRecord, SourceDocument
from .store import get_knowledge_base_catalog

warnings.filterwarnings("ignore", message=".*pkg_resources.*")

QUERY_REWRITE_PROMPT = load_prompt("rag_query_rewrite")
MULTI_QUERY_PROMPT = load_prompt("rag_multi_query")
HYDE_PROMPT = load_prompt("rag_hyde")


def _tokenize(text: str) -> list[str]:
    return list(jieba.cut(text))


@dataclass
class SearchResult:
    id: str
    text: str
    score: float
    source: str = ""
    metadata: dict | None = None
    context_text: str | None = None
    citation: str | None = None


class BM25Index:
    def __init__(self):
        self._index: BM25Okapi | None = None
        self._chunk_map: dict[str, ChunkRecord] = {}
        self._chunk_ids: list[str] = []

    def build(self, chunks: list[ChunkRecord]):
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        self._chunk_ids = [chunk.chunk_id for chunk in chunks]
        tokenized = [_tokenize(chunk.sparse_text or chunk.indexed_text or chunk.text) for chunk in chunks]
        self._index = BM25Okapi(tokenized) if tokenized else None

    def search(
        self,
        query: str,
        top_k: int = 10,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[tuple[str, float]]:
        if self._index is None:
            return []
        tokenized_query = _tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[tuple[str, float]] = []
        for idx, score in ranked:
            if score <= 0:
                continue
            chunk_id = self._chunk_ids[idx]
            chunk = self._chunk_map.get(chunk_id)
            if chunk is None or not _matches_metadata_filters(chunk, metadata_filters):
                continue
            results.append((chunk_id, float(score)))
            if len(results) >= top_k:
                break
        return results


def reciprocal_rank_fusion(
    dense_results: list[tuple[str, float]],
    sparse_results: list[tuple[str, float]],
    k: int = 60,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for rank, (chunk_id, _) in enumerate(dense_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, (chunk_id, _) in enumerate(sparse_results):
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _looks_like_question(query: str) -> bool:
    normalized = query.strip()
    if not normalized:
        return False
    question_tokens = (
        "?",
        "？",
        "什么",
        "如何",
        "怎么",
        "怎样",
        "为何",
        "为什么",
        "是否",
        "哪种",
        "哪些",
        "谁",
        "when",
        "what",
        "why",
        "how",
        "which",
    )
    lowered = normalized.casefold()
    return any(token in normalized or token in lowered for token in question_tokens)


def _matches_metadata_filters(
    chunk: ChunkRecord,
    metadata_filters: dict[str, object] | None = None,
) -> bool:
    if not metadata_filters:
        return True

    metadata = chunk.metadata or {}
    for key, expected in metadata_filters.items():
        if expected is None:
            continue
        if key == "page_number":
            actual = chunk.page_number
        elif key == "section_index":
            actual = chunk.section_index
        else:
            actual = metadata.get(key)
        if actual != expected:
            return False
    return True


def _structural_score_boost(query: str, chunk: ChunkRecord) -> float:
    metadata = chunk.metadata or {}
    normalized_query = query.casefold()
    boost = 0.0

    code_terms = ("代码", "示例", "sample", "code", "snippet", "实现", "函数", "脚本", "命令")
    list_terms = ("步骤", "清单", "列表", "排查", "检查", "要点", "总结", "事项")
    heading_terms = ("概述", "介绍", "是什么", "总览", "目录", "章节", "section", "overview")

    if metadata.get("block_type") == "code" or metadata.get("has_code") is True:
        if any(term in normalized_query for term in code_terms):
            boost += 0.02
    if metadata.get("block_type") == "list" or metadata.get("has_list") is True:
        if any(term in normalized_query for term in list_terms):
            boost += 0.015
    if metadata.get("block_type") == "heading":
        if any(term in normalized_query for term in heading_terms):
            boost += 0.01
        heading_depth = metadata.get("heading_depth")
        if isinstance(heading_depth, int) and heading_depth > 0:
            boost += max(0.0, 0.005 - ((heading_depth - 1) * 0.001))

    return boost


def result_display_text(result: SearchResult) -> str:
    context_text = result.context_text
    if isinstance(context_text, str) and context_text.strip():
        return context_text
    return result.text


def result_citation(result: SearchResult) -> str:
    if isinstance(result.citation, str) and result.citation.strip():
        return result.citation

    metadata = result.metadata or {}
    source_uri = str(metadata.get("source_uri") or result.id)
    labels: list[str] = []
    section_title = metadata.get("section_title")
    if isinstance(section_title, str) and section_title.strip():
        labels.append(section_title.strip())
    page_number = metadata.get("page_number")
    if isinstance(page_number, int):
        labels.append(f"Page {page_number}")
    heading_depth = metadata.get("heading_depth")
    if isinstance(heading_depth, int):
        labels.append(f"H{heading_depth}")
    if labels:
        return f"{source_uri} [{' | '.join(labels)}]"
    return source_uri


def rewrite_query(query: str, llm: AgentLLM | None = None) -> str:
    settings = get_settings()
    if not settings.enable_query_rewrite:
        return query
    try:
        llm_client = llm or AgentLLM()
        prompt = QUERY_REWRITE_PROMPT.format(query=query)
        rewritten = llm_client.think([{"role": "user", "content": prompt}], temperature=0, silent=True)
        rewritten = (rewritten or "").strip()
        if len(rewritten) >= 2:
            return rewritten
    except Exception:
        pass
    return query


def expand_queries(query: str, llm: AgentLLM | None = None) -> list[str]:
    settings = get_settings()
    rewritten = rewrite_query(query, llm=llm)
    queries = [rewritten]
    if not settings.enable_multi_query:
        return queries
    try:
        llm_client = llm or AgentLLM()
        prompt = MULTI_QUERY_PROMPT.format(
            query=query,
            rewritten_query=rewritten,
            count=settings.rag_multi_query_count,
        )
        expanded = llm_client.think([{"role": "user", "content": prompt}], temperature=0, silent=True)
        candidates = []
        for line in (expanded or "").splitlines():
            normalized = line.strip().lstrip("-").lstrip("0123456789.").strip()
            if normalized:
                candidates.append(normalized)
        queries.extend(candidates)
    except Exception:
        pass
    return _dedupe_preserve_order([query, *queries])[: max(settings.rag_multi_query_count, 1) + 1]


def generate_hypothetical_document(query: str, llm: AgentLLM | None = None) -> str:
    settings = get_settings()
    if not settings.enable_hyde:
        return ""
    if settings.hyde_question_only and not _looks_like_question(query):
        return ""
    try:
        llm_client = llm or AgentLLM()
        prompt = HYDE_PROMPT.format(query=query)
        response = llm_client.think([{"role": "user", "content": prompt}], temperature=0.2, silent=True)
        return (response or "").strip()
    except Exception:
        return ""


class HybridRetriever:
    def __init__(self, namespace: str = "default"):
        self.namespace = namespace
        self._bm25 = BM25Index()
        self._chunks: dict[str, ChunkRecord] = {}
        self._reranker: "CrossEncoder | None" = None

    def build_bm25(self, documents: list[str]):
        chunks: list[ChunkRecord] = []
        for index, text in enumerate(documents):
            source_id = make_source_id(f"memory://{self.namespace}/doc-{index}")
            document_version = make_document_version(source_id, text)
            document = SourceDocument(
                document_id=document_version,
                kb_id="",
                source_id=source_id,
                source_uri=f"memory://{self.namespace}/doc-{index}",
                document_version=document_version,
                content=text,
                indexed_text=text,
                sparse_text=text,
            )
            chunks.append(
                ChunkRecord(
                    chunk_id=make_chunk_id(document_version, 0, text),
                    kb_id="",
                    document_id=document.document_id,
                    document_version=document_version,
                    chunk_index=0,
                    text=text,
                    indexed_text=text,
                    sparse_text=text,
                    metadata={"source_uri": document.source_uri},
                )
            )
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._bm25.build(chunks)

    def rebuild_from_catalog(self):
        catalog = get_knowledge_base_catalog()
        kb = catalog.get_knowledge_base(self.namespace)
        if kb is None:
            self._chunks = {}
            self._bm25.build([])
            return
        chunks = catalog.list_chunks_by_kb(kb.kb_id)
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._bm25.build(chunks)

    def expand_contexts(
        self,
        results: list[SearchResult],
        window: int | None = None,
        view: str = "section",
    ) -> list[SearchResult]:
        if view != "chunk":
            results = self.merge_results_by_section(results)
        settings = get_settings()
        if not settings.enable_context_expansion:
            return results

        resolved_window = settings.rag_context_window if window is None else window
        max_chunks = settings.rag_context_max_chunks
        if resolved_window <= 0 and max_chunks <= 1:
            return results

        catalog = get_knowledge_base_catalog()
        expanded: list[SearchResult] = []
        for result in results:
            chunk = self._chunks.get(result.id)
            if chunk is None:
                expanded.append(result)
                continue
            context_chunks = self._collect_context_chunks(
                chunk,
                catalog=catalog,
                window=resolved_window,
                max_chunks=max_chunks,
            )
            if not context_chunks:
                expanded.append(result)
                continue

            anchor_index = next(
                (index for index, item in enumerate(context_chunks) if item.chunk_id == chunk.chunk_id),
                None,
            )
            if anchor_index is None:
                anchor_index = 0

            context_parts: list[str] = []
            for index, candidate in enumerate(context_chunks):
                prefix = ">> " if index == anchor_index else ""
                context_parts.append(f"{prefix}{candidate.text}".strip())
            result.context_text = "\n\n".join(context_parts)
            result.citation = self._build_result_citation(chunk, context_chunks)
            expanded.append(result)
        return expanded

    def merge_results_by_section(self, results: list[SearchResult]) -> list[SearchResult]:
        merged: list[SearchResult] = []
        seen_groups: set[tuple[str, object]] = set()

        for result in results:
            chunk = self._chunks.get(result.id)
            if chunk is None:
                merged.append(result)
                continue
            group_key = (
                chunk.document_id,
                chunk.section_index if chunk.section_index is not None else chunk.chunk_index,
            )
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            merged.append(result)
        return merged

    def _collect_context_chunks(
        self,
        anchor: ChunkRecord,
        *,
        catalog,
        window: int,
        max_chunks: int,
    ) -> list[ChunkRecord]:
        selected: list[ChunkRecord] = []
        seen_ids: set[str] = set()

        def append_chunks(chunks: list[ChunkRecord]):
            for candidate in chunks:
                if candidate.chunk_id in seen_ids:
                    continue
                selected.append(candidate)
                seen_ids.add(candidate.chunk_id)
                if len(selected) >= max_chunks:
                    break

        section_chunks_count = 0
        if anchor.section_index is not None:
            section_chunks = catalog.list_section_chunks(anchor.document_id, anchor.section_index)
            append_chunks(section_chunks)
            section_chunks_count = len(section_chunks)
        if len(selected) < max_chunks and section_chunks_count <= 1 and anchor.page_number is not None:
            page_chunks = [
                chunk
                for chunk in catalog.list_chunks(anchor.document_id)
                if chunk.page_number == anchor.page_number
            ]
            append_chunks(page_chunks)
        if len(selected) < max_chunks and section_chunks_count <= 1 and window > 0:
            append_chunks(
                catalog.list_neighbor_chunks(
                    anchor.document_id,
                    anchor.chunk_index,
                    window=window,
                )
            )

        if not selected:
            return []
        selected.sort(key=lambda item: item.chunk_index)
        return selected[:max_chunks]

    def _build_result_citation(self, anchor: ChunkRecord, context_chunks: list[ChunkRecord]) -> str:
        metadata = anchor.metadata or {}
        source_uri = str(metadata.get("source_uri") or anchor.document_id)
        labels: list[str] = []

        section_title = metadata.get("section_title")
        if isinstance(section_title, str) and section_title.strip():
            labels.append(section_title.strip())
        elif context_chunks:
            section_titles = [
                str(chunk.metadata.get("section_title")).strip()
                for chunk in context_chunks
                if (
                    isinstance(chunk.metadata.get("section_title"), str)
                    and str(chunk.metadata.get("section_title")).strip()
                )
            ]
            if section_titles:
                labels.append(section_titles[0])

        page_numbers = sorted(
            {
                chunk.page_number
                for chunk in context_chunks
                if isinstance(chunk.page_number, int)
            }
        )
        if page_numbers:
            if len(page_numbers) == 1:
                labels.append(f"Page {page_numbers[0]}")
            else:
                labels.append(f"Page {page_numbers[0]}-{page_numbers[-1]}")

        heading_depth = metadata.get("heading_depth")
        if isinstance(heading_depth, int):
            labels.append(f"H{heading_depth}")

        if labels:
            return f"{source_uri} [{' | '.join(labels)}]"
        return source_uri

    def load_reranker(self, model_name: str | None = None):
        try:
            from sentence_transformers import CrossEncoder
        except Exception:
            self._reranker = None
            return

        settings = get_settings()
        try:
            self._reranker = CrossEncoder(model_name or settings.reranker_model)
        except Exception:
            self._reranker = None

    def search(
        self,
        query: str,
        dense_results: list[tuple[str, float]],
        top_k: int = 5,
        rrf_k: int = 60,
        min_score: float = 0.0,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[SearchResult]:
        sparse = self._bm25.search(query, top_k=top_k * 2, metadata_filters=metadata_filters)
        fused = reciprocal_rank_fusion(dense_results, sparse, k=rrf_k)
        boosted: list[tuple[str, float]] = []
        for chunk_id, score in fused.items():
            chunk = self._chunks.get(chunk_id)
            if chunk is None or not _matches_metadata_filters(chunk, metadata_filters):
                continue
            boosted.append((chunk_id, score + _structural_score_boost(query, chunk)))
        ranked = sorted(boosted, key=lambda x: x[1], reverse=True)[:top_k * 2]

        if self._reranker is not None and len(ranked) > 1:
            return self._rerank(
                query,
                ranked,
                top_k,
                min_score=min_score,
                metadata_filters=metadata_filters,
            )

        return [
            SearchResult(
                id=chunk_id,
                text=self._chunks[chunk_id].text,
                score=score,
                source="rrf",
                metadata=self._chunks[chunk_id].metadata,
            )
            for chunk_id, score in ranked[:top_k]
            if chunk_id in self._chunks
        ]

    def _rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
        top_k: int,
        min_score: float = 0.3,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[SearchResult]:
        pairs = [(query, self._chunks[chunk_id].text) for chunk_id, _ in candidates if chunk_id in self._chunks]
        chunk_ids = [chunk_id for chunk_id, _ in candidates if chunk_id in self._chunks]
        scores = self._reranker.predict(pairs)
        scored = [(chunk_id, float(score)) for chunk_id, score in zip(chunk_ids, scores)]
        filtered = [
            item
            for item in scored
            if item[1] >= min_score
            and _matches_metadata_filters(self._chunks[item[0]], metadata_filters)
        ]
        reranked = sorted(filtered, key=lambda x: x[1], reverse=True)[:top_k]
        return [
            SearchResult(
                id=chunk_id,
                text=self._chunks[chunk_id].text,
                score=score,
                source="rerank",
                metadata=self._chunks[chunk_id].metadata,
            )
            for chunk_id, score in reranked
        ]


_retriever: HybridRetriever | None = None


def _get_retriever(namespace: str = "default", docs: list[str] | None = None) -> HybridRetriever:
    global _retriever
    if _retriever is None or _retriever.namespace != namespace:
        _retriever = HybridRetriever(namespace=namespace)
        if docs:
            _retriever.build_bm25(docs)
        else:
            _retriever.rebuild_from_catalog()
    elif docs:
        _retriever.build_bm25(docs)
    return _retriever


def build_knowledge_base(documents: list[str], load_reranker: bool = True, namespace: str = "default"):
    from .chroma_client import delete_collection

    global _retriever

    delete_collection(namespace=namespace)

    catalog = get_knowledge_base_catalog()
    collection_name = resolve_collection_name(namespace=namespace)
    existing_kb = catalog.get_knowledge_base(namespace)
    if existing_kb is not None:
        catalog.delete_knowledge_base(existing_kb.kb_id)
    kb_record = KnowledgeBaseRecord(
        kb_id=collection_name,
        namespace=namespace,
        display_name=namespace,
        collection_name=collection_name,
    )
    catalog.upsert_knowledge_base(kb_record)

    document_records: list[SourceDocument] = []
    chunk_records: list[ChunkRecord] = []
    chunk_texts: list[str] = []
    chunk_metadatas: list[dict] = []
    chunk_ids: list[str] = []

    for index, text in enumerate(documents):
        source_id = make_source_id(f"memory://{namespace}/doc-{index}")
        document_version = make_document_version(source_id, text)
        document = SourceDocument(
            document_id=document_version,
            kb_id=kb_record.kb_id,
            source_id=source_id,
            source_uri=f"memory://{namespace}/doc-{index}",
            document_version=document_version,
            content=text,
            indexed_text=text,
            sparse_text=text,
        )
        document_records.append(document)
        chunk = ChunkRecord(
            chunk_id=make_chunk_id(document_version, 0, text),
            kb_id=kb_record.kb_id,
            document_id=document.document_id,
            document_version=document_version,
            chunk_index=0,
            text=text,
            indexed_text=text,
            sparse_text=text,
            metadata={"source_uri": document.source_uri},
        )
        chunk_records.append(chunk)
        chunk_texts.append(chunk.text)
        chunk_metadatas.append(chunk.metadata)
        chunk_ids.append(chunk.chunk_id)

    catalog.upsert_documents(document_records)
    catalog.upsert_chunks(chunk_records)
    insert_documents(chunk_texts, metadatas=chunk_metadatas, ids=chunk_ids, namespace=namespace)

    retriever = HybridRetriever(namespace=namespace)
    retriever._chunks = {chunk.chunk_id: chunk for chunk in chunk_records}
    retriever._bm25.build(chunk_records)
    if load_reranker:
        retriever.load_reranker()
    _retriever = retriever


def search_knowledge_base(query: str, namespace: str = "default") -> str:
    retriever = _get_retriever(namespace=namespace)
    if not retriever._chunks:
        return "知识库为空，请先用 `nexus kb add` 添加文档。"
    if retriever._reranker is None:
        retriever.load_reranker()
    queries = expand_queries(query)
    hypothetical_document = generate_hypothetical_document(query)
    dense_fused: dict[str, float] = {}
    for search_query in queries:
        dense_results = chroma_search(search_query, limit=10, namespace=namespace)
        for rank, item in enumerate(dense_results):
            dense_fused[item["id"]] = dense_fused.get(item["id"], 0.0) + 1.0 / (60 + rank + 1)
    if hypothetical_document:
        hyde_results = chroma_search(hypothetical_document, limit=10, namespace=namespace)
        for rank, item in enumerate(hyde_results):
            dense_fused[item["id"]] = dense_fused.get(item["id"], 0.0) + 0.8 / (60 + rank + 1)
    dense_results = sorted(dense_fused.items(), key=lambda x: x[1], reverse=True)
    if not dense_results:
        return "未找到相关知识。"
    results = retriever.search(query, dense_results, top_k=5)
    if not results:
        return "未找到相关知识。"
    results = retriever.expand_contexts(results)
    return "\n\n".join(
        f"[{index + 1}] {result_citation(result)} (相关度:{result.score:.2f}) {result_display_text(result)}"
        for index, result in enumerate(results)
    )

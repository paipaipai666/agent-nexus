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


def _tokenize(text: str) -> list[str]:
    return list(jieba.cut(text))


@dataclass
class SearchResult:
    id: str
    text: str
    score: float
    source: str = ""
    metadata: dict | None = None


class BM25Index:
    def __init__(self):
        self._index: BM25Okapi | None = None
        self._chunk_map: dict[str, ChunkRecord] = {}

    def build(self, chunks: list[ChunkRecord]):
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        tokenized = [_tokenize(chunk.sparse_text or chunk.indexed_text or chunk.text) for chunk in chunks]
        self._index = BM25Okapi(tokenized) if tokenized else None

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        if self._index is None:
            return []
        chunk_ids = list(self._chunk_map.keys())
        tokenized_query = _tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(chunk_ids[idx], float(score)) for idx, score in ranked[:top_k] if score > 0]


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

    def search(self, query: str, dense_results: list[tuple[str, float]], top_k: int = 5,
               rrf_k: int = 60, min_score: float = 0.0) -> list[SearchResult]:
        sparse = self._bm25.search(query, top_k=top_k * 2)
        fused = reciprocal_rank_fusion(dense_results, sparse, k=rrf_k)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k * 2]

        if self._reranker is not None and len(ranked) > 1:
            return self._rerank(query, ranked, top_k, min_score=min_score)

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

    def _rerank(self, query: str, candidates: list[tuple[str, float]], top_k: int,
                 min_score: float = 0.3) -> list[SearchResult]:
        pairs = [(query, self._chunks[chunk_id].text) for chunk_id, _ in candidates if chunk_id in self._chunks]
        chunk_ids = [chunk_id for chunk_id, _ in candidates if chunk_id in self._chunks]
        scores = self._reranker.predict(pairs)
        scored = [(chunk_id, float(score)) for chunk_id, score in zip(chunk_ids, scores)]
        filtered = [item for item in scored if item[1] >= min_score]
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
    dense_fused: dict[str, float] = {}
    for search_query in queries:
        dense_results = chroma_search(search_query, limit=10, namespace=namespace)
        for rank, item in enumerate(dense_results):
            dense_fused[item["id"]] = dense_fused.get(item["id"], 0.0) + 1.0 / (60 + rank + 1)
    dense_results = sorted(dense_fused.items(), key=lambda x: x[1], reverse=True)
    if not dense_results:
        return "未找到相关知识。"
    results = retriever.search(query, dense_results, top_k=5)
    if not results:
        return "未找到相关知识。"
    return "\n\n".join(
        f"[{index + 1}] {result.id} (相关度:{result.score:.2f}) {result.text}"
        for index, result in enumerate(results)
    )

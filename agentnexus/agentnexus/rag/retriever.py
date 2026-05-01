from dataclasses import dataclass

import jieba
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder

from .chroma_client import get_embedding_model, insert_documents, search as chroma_search


def _tokenize(text: str) -> list[str]:
    return list(jieba.cut(text))


@dataclass
class SearchResult:
    id: str
    text: str
    score: float
    source: str = ""


class BM25Index:
    def __init__(self):
        self._index: BM25Okapi | None = None
        self._docs: list[str] = []

    def build(self, documents: list[str]):
        self._docs = list(documents)
        tokenized = [_tokenize(doc) for doc in documents]
        self._index = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        if self._index is None:
            return []
        tokenized_query = _tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [(idx, float(score)) for idx, score in ranked[:top_k] if score > 0]


def reciprocal_rank_fusion(
    dense_results: list[tuple[int, float]],
    sparse_results: list[tuple[int, float]],
    k: int = 60,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    for rank, (doc_id, _) in enumerate(dense_results):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, (doc_id, _) in enumerate(sparse_results):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class HybridRetriever:
    def __init__(self):
        self._bm25 = BM25Index()
        self._docs: list[str] = []
        self._reranker: CrossEncoder | None = None

    def build_bm25(self, documents: list[str]):
        self._docs = list(documents)
        self._bm25.build(documents)

    def load_reranker(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self._reranker = CrossEncoder(model_name)

    def search(self, query: str, dense_results: list[tuple[int, float]], top_k: int = 5, rrf_k: int = 60) -> list[SearchResult]:
        sparse = self._bm25.search(query, top_k=top_k * 2)
        fused = reciprocal_rank_fusion(dense_results, sparse, k=rrf_k)
        ranked = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k * 2]

        if self._reranker is not None and len(ranked) > 1:
            return self._rerank(query, ranked, top_k)

        return [
            SearchResult(id=str(doc_id), text=self._docs[doc_id], score=score, source="rrf")
            for doc_id, score in ranked[:top_k]
        ]

    def _rerank(self, query: str, candidates: list[tuple[int, float]], top_k: int) -> list[SearchResult]:
        pairs = [(query, self._docs[doc_id]) for doc_id, _ in candidates]
        scores = self._reranker.predict(pairs)
        reranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            SearchResult(id=str(doc_id), text=self._docs[doc_id], score=float(score), source="rerank")
            for (doc_id, _), score in reranked
        ]


_retriever: HybridRetriever | None = None


def _get_retriever(docs: list[str] | None = None) -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
        if docs:
            _retriever.build_bm25(docs)
    return _retriever


def build_knowledge_base(documents: list[str], load_reranker: bool = True):
    from .chroma_client import delete_collection
    delete_collection()
    insert_documents(documents)
    _get_retriever(documents)
    if load_reranker:
        _get_retriever().load_reranker()


def search_knowledge_base(query: str) -> str:
    retriever = _get_retriever()
    if not retriever._docs:
        return "知识库为空，请先用 `nexus kb add` 添加文档。"
    model = get_embedding_model()
    vec = model.encode(query, normalize_embeddings=True).tolist()
    dense_results = chroma_search(query, limit=10)
    if not dense_results:
        return "未找到相关知识。"
    dense = [(i, r["score"]) for i, r in enumerate(dense_results)]
    results = retriever.search(query, dense, top_k=5)
    if not results:
        return "未找到相关知识。"
    return "\n\n".join(f"[{i+1}] (相关度:{r.score:.2f}) {r.text}" for i, r in enumerate(results))

"""Sparse ranking and fusion helpers for hybrid retrieval."""

from __future__ import annotations

import jieba
from rank_bm25 import BM25Okapi

from .models import ChunkRecord


def tokenize(text: str) -> list[str]:
    return list(jieba.cut(text))


def matches_metadata_filters(
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


class BM25Index:
    def __init__(self):
        self._index: BM25Okapi | None = None
        self._chunk_map: dict[str, ChunkRecord] = {}
        self._chunk_ids: list[str] = []

    def build(self, chunks: list[ChunkRecord]):
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        self._chunk_ids = [chunk.chunk_id for chunk in chunks]
        tokenized = [tokenize(chunk.sparse_text or chunk.indexed_text or chunk.text) for chunk in chunks]
        self._index = BM25Okapi(tokenized) if tokenized else None

    def search(
        self,
        query: str,
        top_k: int = 10,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[tuple[str, float]]:
        if self._index is None:
            return []
        tokenized_query = tokenize(query)
        scores = self._index.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[tuple[str, float]] = []
        for idx, score in ranked:
            if score <= 0:
                continue
            chunk_id = self._chunk_ids[idx]
            chunk = self._chunk_map.get(chunk_id)
            if chunk is None or not matches_metadata_filters(chunk, metadata_filters):
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


def structural_score_boost(query: str, chunk: ChunkRecord) -> float:
    metadata = chunk.metadata or {}
    normalized_query = query.casefold()
    boost = 0.0

    code_terms = ("代码", "示例", "sample", "code", "snippet", "实现", "函数", "脚本", "命令")
    list_terms = ("步骤", "清单", "列表", "排查", "检查", "要点", "总结", "事项")
    heading_terms = ("概览", "介绍", "是什么", "总览", "目录", "章节", "section", "overview")

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

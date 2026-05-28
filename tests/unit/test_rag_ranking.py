from agentnexus.rag.models import ChunkRecord
from agentnexus.rag.ranking import (
    BM25Index,
    matches_metadata_filters,
    reciprocal_rank_fusion,
    structural_score_boost,
    tokenize,
)


def _make_chunk(chunk_id, text="hello world", metadata=None, **kwargs):
    defaults = dict(
        chunk_id=chunk_id,
        kb_id="kb_test",
        document_id="doc_v1",
        document_version="v1",
        chunk_index=0,
        text=text,
        metadata=metadata or {},
    )
    defaults.update(kwargs)
    return ChunkRecord(**defaults)


class TestTokenize:
    def test_basic_text(self):
        tokens = tokenize("hello world")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_empty_string(self):
        tokens = tokenize("")
        assert isinstance(tokens, list)


class TestMatchesMetadataFilters:
    def test_no_filters(self):
        chunk = _make_chunk("c1")
        assert matches_metadata_filters(chunk) is True
        assert matches_metadata_filters(chunk, None) is True
        assert matches_metadata_filters(chunk, {}) is True

    def test_matching_page_number(self):
        chunk = _make_chunk("c1", page_number=5)
        assert matches_metadata_filters(chunk, {"page_number": 5}) is True

    def test_mismatched_page_number(self):
        chunk = _make_chunk("c1", page_number=5)
        assert matches_metadata_filters(chunk, {"page_number": 3}) is False

    def test_matching_section_index(self):
        chunk = _make_chunk("c1", section_index=2)
        assert matches_metadata_filters(chunk, {"section_index": 2}) is True

    def test_mismatched_section_index(self):
        chunk = _make_chunk("c1", section_index=2)
        assert matches_metadata_filters(chunk, {"section_index": 0}) is False

    def test_matching_metadata_key(self):
        chunk = _make_chunk("c1", metadata={"format": "markdown"})
        assert matches_metadata_filters(chunk, {"format": "markdown"}) is True

    def test_mismatched_metadata_key(self):
        chunk = _make_chunk("c1", metadata={"format": "markdown"})
        assert matches_metadata_filters(chunk, {"format": "html"}) is False

    def test_none_filter_value_is_skipped(self):
        chunk = _make_chunk("c1")
        assert matches_metadata_filters(chunk, {"page_number": None}) is True

    def test_multiple_filters_all_pass(self):
        chunk = _make_chunk("c1", page_number=3, section_index=1, metadata={"lang": "en"})
        assert matches_metadata_filters(chunk, {"page_number": 3, "lang": "en"}) is True

    def test_multiple_filters_one_fails(self):
        chunk = _make_chunk("c1", page_number=3, metadata={"lang": "en"})
        assert matches_metadata_filters(chunk, {"page_number": 3, "lang": "zh"}) is False


class TestBM25Index:
    def _make_corpus(self):
        return [
            _make_chunk("c0", text="python programming language guide"),
            _make_chunk("c1", text="python guide tutorial basics"),
            _make_chunk("c2", text="java programming tutorial advanced"),
            _make_chunk("c3", text="cooking recipe book ingredients"),
            _make_chunk("c4", text="data science machine learning"),
            _make_chunk("c5", text="web development javascript html"),
        ]

    def test_build_and_search_basic(self):
        chunks = self._make_corpus()
        index = BM25Index()
        index.build(chunks)
        results = index.search("python guide", top_k=5)
        assert len(results) > 0
        assert results[0][1] > 0

    def test_empty_chunks(self):
        index = BM25Index()
        index.build([])
        results = index.search("anything", top_k=5)
        assert results == []

    def test_search_with_metadata_filters(self):
        chunks = [
            _make_chunk("c0", text="python programming language guide"),
            _make_chunk("c1", text="python guide tutorial basics", page_number=1),
            _make_chunk("c2", text="java programming tutorial advanced", page_number=2),
            _make_chunk("c3", text="cooking recipe book ingredients"),
            _make_chunk("c4", text="data science machine learning"),
            _make_chunk("c5", text="web development javascript html"),
        ]
        index = BM25Index()
        index.build(chunks)
        results = index.search("python", top_k=5, metadata_filters={"page_number": 1})
        chunk_ids = [r[0] for r in results]
        assert "c1" in chunk_ids
        assert "c2" not in chunk_ids

    def test_filters_out_zero_scores(self):
        chunks = self._make_corpus()
        index = BM25Index()
        index.build(chunks)
        results = index.search("nonexistent_term_xyz", top_k=5)
        for _, score in results:
            assert score > 0

    def test_top_k_limits_results(self):
        chunks = [_make_chunk(f"c{i}", text=f"document about topic number {i} content") for i in range(20)]
        index = BM25Index()
        index.build(chunks)
        results = index.search("document topic", top_k=3)
        assert len(results) <= 3

    def test_uses_sparse_text_over_indexed(self):
        chunks = [
            _make_chunk("c0", text="alpha beta gamma delta epsilon"),
            _make_chunk("c1", text="alpha beta gamma delta epsilon", sparse_text="unique_sparse_keywords"),
            _make_chunk("c2", text="zeta eta theta iota kappa"),
            _make_chunk("c3", text="lambda mu nu xi omicron"),
            _make_chunk("c4", text="pi rho sigma tau upsilon"),
        ]
        index = BM25Index()
        index.build(chunks)
        results = index.search("unique_sparse_keywords", top_k=5)
        chunk_ids = [r[0] for r in results]
        assert "c1" in chunk_ids


class TestReciprocalRankFusion:
    def test_basic_fusion(self):
        dense = [("c1", 0.9), ("c2", 0.8)]
        sparse = [("c2", 12.0), ("c1", 10.0)]
        result = reciprocal_rank_fusion(dense, sparse, k=60)
        assert "c1" in result
        assert "c2" in result
        assert result["c1"] > 0
        assert result["c2"] > 0

    def test_overlapping_chunk_ids_sum_scores(self):
        dense = [("c1", 0.9)]
        sparse = [("c1", 10.0)]
        result = reciprocal_rank_fusion(dense, sparse, k=60)
        expected = 1.0 / (60 + 1) + 1.0 / (60 + 1)
        assert abs(result["c1"] - expected) < 1e-9

    def test_non_overlapping_chunk_ids(self):
        dense = [("c1", 0.9)]
        sparse = [("c2", 10.0), ("c3", 8.0)]
        result = reciprocal_rank_fusion(dense, sparse, k=60)
        assert "c1" in result
        assert "c2" in result
        assert "c3" in result
        assert result["c2"] > result["c3"]

    def test_empty_dense(self):
        sparse = [("c1", 10.0)]
        result = reciprocal_rank_fusion([], sparse, k=60)
        assert result["c1"] == 1.0 / 61

    def test_empty_sparse(self):
        dense = [("c1", 0.9)]
        result = reciprocal_rank_fusion(dense, [], k=60)
        assert result["c1"] == 1.0 / 61

    def test_both_empty(self):
        result = reciprocal_rank_fusion([], [], k=60)
        assert result == {}

    def test_rank_order_matters(self):
        dense = [("c1", 0.9), ("c2", 0.8)]
        sparse = [("c3", 10.0), ("c2", 9.0), ("c1", 8.0)]
        result = reciprocal_rank_fusion(dense, sparse, k=60)
        assert result["c1"] > result["c2"]


class TestStructuralScoreBoost:
    def test_code_block_with_code_query(self):
        chunk = _make_chunk("c1", metadata={"block_type": "code"})
        boost = structural_score_boost("show me the code snippet", chunk)
        assert boost == 0.02

    def test_code_block_no_match(self):
        chunk = _make_chunk("c1", metadata={"block_type": "code"})
        boost = structural_score_boost("general overview", chunk)
        assert boost == 0.0

    def test_has_code_flag_with_code_query(self):
        chunk = _make_chunk("c1", metadata={"has_code": True})
        boost = structural_score_boost("函数实现示例", chunk)
        assert boost == 0.02

    def test_list_block_with_list_query(self):
        chunk = _make_chunk("c1", metadata={"block_type": "list"})
        boost = structural_score_boost("操作步骤清单", chunk)
        assert boost == 0.015

    def test_has_list_flag_with_list_query(self):
        chunk = _make_chunk("c1", metadata={"has_list": True})
        boost = structural_score_boost("检查清单", chunk)
        assert boost == 0.015

    def test_heading_block_with_heading_query(self):
        chunk = _make_chunk("c1", metadata={"block_type": "heading", "heading_depth": 1})
        boost = structural_score_boost("overview of the system", chunk)
        assert boost > 0.01

    def test_heading_depth_contributes_extra(self):
        chunk_h1 = _make_chunk("c1", metadata={"block_type": "heading", "heading_depth": 1})
        chunk_h3 = _make_chunk("c2", metadata={"block_type": "heading", "heading_depth": 3})
        boost_h1 = structural_score_boost("概览", chunk_h1)
        boost_h3 = structural_score_boost("概览", chunk_h3)
        assert boost_h1 > boost_h3

    def test_no_metadata_returns_zero(self):
        chunk = _make_chunk("c1", metadata={})
        boost = structural_score_boost("code snippet", chunk)
        assert boost == 0.0

    def test_no_match_returns_zero(self):
        chunk = _make_chunk("c1", metadata={"block_type": "paragraph"})
        boost = structural_score_boost("random query", chunk)
        assert boost == 0.0

"""Tests for agentnexus.server.pagination."""

import pytest

from agentnexus.server.pagination import PageParams, paginate


class TestPageParams:
    def test_default_values(self):
        params = PageParams()
        assert params.offset == 0
        assert params.limit == 20

    def test_custom_values(self):
        params = PageParams(offset=10, limit=50)
        assert params.offset == 10
        assert params.limit == 50

    def test_partial_custom_offset(self):
        params = PageParams(offset=5)
        assert params.offset == 5
        assert params.limit == 20

    def test_partial_custom_limit(self):
        params = PageParams(limit=50)
        assert params.offset == 0
        assert params.limit == 50


class TestPaginate:
    def test_normal_pagination(self):
        items = list(range(50))
        params = PageParams(offset=0, limit=10)
        result = paginate(items, params)
        assert result["items"] == list(range(10))
        assert result["total"] == 50
        assert result["offset"] == 0
        assert result["limit"] == 10
        assert result["has_more"] is True

    def test_has_more_false_at_end(self):
        items = list(range(50))
        params = PageParams(offset=40, limit=10)
        result = paginate(items, params)
        assert result["items"] == list(range(40, 50))
        assert result["has_more"] is False

    def test_empty_list(self):
        result = paginate([], PageParams())
        assert result["items"] == []
        assert result["total"] == 0
        assert result["has_more"] is False

    def test_offset_beyond_total(self):
        items = list(range(10))
        params = PageParams(offset=100, limit=10)
        result = paginate(items, params)
        assert result["items"] == []
        assert result["total"] == 10
        assert result["has_more"] is False

    def test_offset_plus_limit_equals_total_has_more_false(self):
        items = list(range(20))
        params = PageParams(offset=10, limit=10)
        result = paginate(items, params)
        assert len(result["items"]) == 10
        assert result["has_more"] is False

    def test_offset_plus_limit_less_than_total_has_more_true(self):
        items = list(range(20))
        params = PageParams(offset=9, limit=10)
        result = paginate(items, params)
        assert len(result["items"]) == 10
        assert result["has_more"] is True

    def test_limit_larger_than_list(self):
        items = list(range(50))
        params = PageParams(offset=0, limit=100)
        result = paginate(items, params)
        assert len(result["items"]) == 50
        assert result["has_more"] is False

    def test_mixed_types(self):
        items = [1, "two", 3.0, None, True]
        params = PageParams(offset=1, limit=3)
        result = paginate(items, params)
        assert result["items"] == ["two", 3.0, None]
        assert result["total"] == 5
        assert result["has_more"] is True

    def test_single_item(self):
        items = [42]
        params = PageParams(offset=0, limit=20)
        result = paginate(items, params)
        assert result["items"] == [42]
        assert result["total"] == 1
        assert result["has_more"] is False

    def test_offset_zero_limit_one(self):
        items = list(range(5))
        params = PageParams(offset=0, limit=1)
        result = paginate(items, params)
        assert result["items"] == [0]
        assert result["has_more"] is True

    def test_result_keys_present(self):
        result = paginate([1, 2, 3], PageParams())
        assert set(result.keys()) == {"items", "total", "offset", "limit", "has_more"}

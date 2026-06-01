"""Tests for agentnexus.evaluation.utils: JSONL trace loading helpers."""

import json
from pathlib import Path

import pytest

from agentnexus.evaluation.utils import (
    find_trace,
    find_trace_in_file,
    iter_spans,
    load_all_traces,
    load_trace_spans,
)


class TestLoadTraceSpans:
    """Tests for load_trace_spans."""

    def test_valid_jsonl_multiple_spans_grouped(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        spans = [
            {"trace_id": "t1", "name": "a"},
            {"trace_id": "t2", "name": "b"},
            {"trace_id": "t1", "name": "c"},
        ]
        f.write_text("\n".join(json.dumps(s) for s in spans), encoding="utf-8")

        result = load_trace_spans(f)

        assert set(result.keys()) == {"t1", "t2"}
        assert len(result["t1"]) == 2
        assert len(result["t2"]) == 1
        assert result["t1"][0]["name"] == "a"
        assert result["t1"][1]["name"] == "c"

    def test_empty_lines_skipped(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        content = '{"trace_id":"t1","name":"a"}\n\n\n{"trace_id":"t1","name":"b"}\n'
        f.write_text(content, encoding="utf-8")

        result = load_trace_spans(f)

        assert len(result["t1"]) == 2

    def test_malformed_json_skipped(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        content = '{"trace_id":"t1","name":"a"}\nNOT JSON\n{"trace_id":"t1","name":"b"}\n'
        f.write_text(content, encoding="utf-8")

        result = load_trace_spans(f)

        assert len(result["t1"]) == 2

    def test_missing_trace_id_defaults_to_unknown(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        spans = [
            {"name": "no_id"},
            {"trace_id": "t1", "name": "with_id"},
        ]
        f.write_text("\n".join(json.dumps(s) for s in spans), encoding="utf-8")

        result = load_trace_spans(f)

        assert "unknown" in result
        assert "t1" in result
        assert len(result["unknown"]) == 1

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_trace_spans(tmp_path / "nonexistent.jsonl")

    def test_path_as_string(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text('{"trace_id":"t1","name":"a"}\n', encoding="utf-8")

        result = load_trace_spans(str(f))

        assert "t1" in result


class TestFindTraceInFile:
    """Tests for find_trace_in_file."""

    def test_returns_matching_spans(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        spans = [
            {"trace_id": "t1", "name": "a"},
            {"trace_id": "t2", "name": "b"},
            {"trace_id": "t1", "name": "c"},
        ]
        f.write_text("\n".join(json.dumps(s) for s in spans), encoding="utf-8")

        result = find_trace_in_file(f, "t1")

        assert result is not None
        assert len(result) == 2
        assert all(s["trace_id"] == "t1" for s in result)

    def test_returns_none_when_no_match(self, tmp_path):
        f = tmp_path / "traces.jsonl"
        f.write_text('{"trace_id":"t1","name":"a"}\n', encoding="utf-8")

        result = find_trace_in_file(f, "nonexistent")

        assert result is None

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("", encoding="utf-8")

        result = find_trace_in_file(f, "t1")

        assert result is None


class TestFindTrace:
    """Tests for find_trace."""

    def test_returns_first_match_newest_first(self, tmp_path):
        older = tmp_path / "a_older.jsonl"
        newer = tmp_path / "b_newer.jsonl"
        older.write_text(
            json.dumps({"trace_id": "t1", "source": "old"}) + "\n",
            encoding="utf-8",
        )
        newer.write_text(
            json.dumps({"trace_id": "t1", "source": "new"}) + "\n",
            encoding="utf-8",
        )

        result = find_trace(tmp_path, "t1")

        assert result is not None
        # glob sorted reverse means "b_newer" comes first
        assert result[0]["source"] == "new"

    def test_returns_none_when_not_found(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            '{"trace_id":"t1","name":"a"}\n', encoding="utf-8"
        )

        result = find_trace(tmp_path, "nonexistent")

        assert result is None

    def test_empty_directory_returns_none(self, tmp_path):
        result = find_trace(tmp_path, "t1")

        assert result is None


class TestIterSpans:
    """Tests for iter_spans."""

    def test_yields_all_spans_with_no_filter(self, tmp_path):
        (tmp_path / "a.jsonl").write_text(
            '{"trace_id":"t1","name":"a"}\n{"trace_id":"t2","name":"b"}\n',
            encoding="utf-8",
        )
        (tmp_path / "b.jsonl").write_text(
            '{"trace_id":"t3","name":"c"}\n',
            encoding="utf-8",
        )

        result = list(iter_spans(tmp_path))

        assert len(result) == 3
        names = {s["name"] for s in result}
        assert names == {"a", "b", "c"}

    def test_filter_fn_matching_some(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            '{"trace_id":"t1","score":0.9}\n{"trace_id":"t2","score":0.3}\n',
            encoding="utf-8",
        )

        result = list(iter_spans(tmp_path, filter_fn=lambda s: s.get("score", 0) > 0.5))

        assert len(result) == 1
        assert result[0]["score"] == 0.9

    def test_filter_fn_matching_nothing(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            '{"trace_id":"t1","score":0.1}\n',
            encoding="utf-8",
        )

        result = list(iter_spans(tmp_path, filter_fn=lambda s: s.get("score", 0) > 0.9))

        assert result == []

    def test_skips_malformed_json(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            '{"trace_id":"t1"}\nBAD\n{"trace_id":"t2"}\n',
            encoding="utf-8",
        )

        result = list(iter_spans(tmp_path))

        assert len(result) == 2


class TestLoadAllTraces:
    """Tests for load_all_traces."""

    def test_groups_spans_from_multiple_files(self, tmp_path):
        (tmp_path / "a.jsonl").write_text(
            '{"trace_id":"t1","name":"a"}\n{"trace_id":"t2","name":"b"}\n',
            encoding="utf-8",
        )
        (tmp_path / "b.jsonl").write_text(
            '{"trace_id":"t1","name":"c"}\n',
            encoding="utf-8",
        )

        result = load_all_traces(tmp_path)

        assert set(result.keys()) == {"t1", "t2"}
        assert len(result["t1"]) == 2
        assert len(result["t2"]) == 1

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        result = load_all_traces(tmp_path)

        assert result == {}

    def test_path_as_string(self, tmp_path):
        (tmp_path / "traces.jsonl").write_text(
            '{"trace_id":"t1","name":"a"}\n',
            encoding="utf-8",
        )

        result = load_all_traces(str(tmp_path))

        assert "t1" in result

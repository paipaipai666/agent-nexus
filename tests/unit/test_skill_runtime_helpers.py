from agentnexus.skills.runtime import (
    WorkflowRunState,
    WorkflowStepState,
    _format_value,
    _format_with_variables,
    _summarize,
    _supported_kb_filters,
    _truncate_block,
)


class TestFormatValue:
    def test_format_string_value(self):
        result = _format_value("{var}", {"var": "hello"})
        assert result == "hello"

    def test_format_dict_value(self):
        result = _format_value({"key": "{var}"}, {"var": "hello"})
        assert result == {"key": "hello"}

    def test_format_list_value(self):
        result = _format_value(["a", "{var}"], {"var": "hello"})
        assert result == ["a", "hello"]

    def test_non_string_passthrough(self):
        assert _format_value(42, {}) == 42
        assert _format_value(True, {}) is True
        assert _format_value(None, {}) is None

    def test_missing_variable_unchanged(self):
        result = _format_value("{missing}", {})
        assert result == "{missing}"


class TestSupportedKbFilters:
    def test_allows_known_filters(self):
        filters = {
            "source": "docs",
            "file_format": "md",
            "section_title": "Intro",
            "page_number": 3,
            "block_type": "code",
            "has_code": True,
            "has_list": False,
            "heading_depth": 2,
        }
        assert _supported_kb_filters(filters) == filters

    def test_filters_unknown_keys(self):
        assert _supported_kb_filters({"unknown": "val"}) == {}

    def test_empty_filters(self):
        assert _supported_kb_filters(None) == {}
        assert _supported_kb_filters({}) == {}

    def test_mixed_filters(self):
        result = _supported_kb_filters({"source": "doc", "unknown": "val", "block_type": "list"})
        assert result == {"source": "doc", "block_type": "list"}


class TestFormatWithVariables:
    def test_format_success(self):
        result = _format_with_variables("Hello {name}", {"name": "world"})
        assert result == "Hello world"

    def test_core_keys_excluded(self):
        variables = {
            "tools": "should_not_appear",
            "question": "should_not_appear",
            "history": "should_not_appear",
            "memory_context": "should_not_appear",
            "conversation_context": "should_not_appear",
            "name": "world",
        }
        result = _format_with_variables("Hello {name}, tools={tools}", variables)
        assert result == "Hello {name}, tools={tools}"

    def test_empty_text(self):
        assert _format_with_variables("", {"var": "val"}) == ""

    def test_no_variables(self):
        assert _format_with_variables("Hello world", {}) == "Hello world"

    def test_missing_variable_graceful(self):
        result = _format_with_variables("{missing}", {"other": "val"})
        assert result == "{missing}"


class TestSummarize:
    def test_short_text_unchanged(self):
        text = "Hello world"
        assert _summarize(text, limit=72) == "Hello world"

    def test_long_text_truncated(self):
        text = "a" * 80
        result = _summarize(text, limit=72)
        assert len(result) == 72
        assert result.endswith("…")

    def test_custom_limit(self):
        text = "a" * 50
        result = _summarize(text, limit=10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_multiline_collapsed(self):
        text = "hello   world\n\nsecond  line"
        assert _summarize(text) == "hello world second line"


class TestTruncateBlock:
    def test_short_block_unchanged(self):
        text = "Hello world"
        assert _truncate_block(text) == "Hello world"

    def test_long_block_truncated(self):
        text = "a" * 5000
        result = _truncate_block(text)
        assert len(result) < 5000
        assert "[truncated workflow context]" in result

    def test_custom_limit_not_supported(self):
        text = "a" * 5000
        result = _truncate_block(text)
        assert "[truncated workflow context]" in result


class TestDurationMsProperty:
    def test_no_timing_returns_zero(self):
        step = WorkflowStepState(id="s1", type="prompt")
        assert step.duration_ms == 0

    def test_calculates_duration(self):
        step = WorkflowStepState(id="s1", type="prompt", started_at=100.0, ended_at=102.5)
        assert step.duration_ms == 2500.0

    def test_rounds_to_one_decimal(self):
        step = WorkflowStepState(id="s1", type="prompt", started_at=100.0, ended_at=101.23456)
        assert step.duration_ms == 1234.6


class TestSkippedCountProperty:
    def test_counts_skipped_steps(self):
        state = WorkflowRunState(
            run_id="test-run",
            question="test",
            workflow_id="test",
            steps=[
                WorkflowStepState(id="s1", type="prompt", status="ok"),
                WorkflowStepState(id="s2", type="prompt", status="skipped"),
                WorkflowStepState(id="s3", type="prompt", status="skipped"),
                WorkflowStepState(id="s4", type="prompt", status="error"),
            ],
        )
        assert state.skipped_count == 2

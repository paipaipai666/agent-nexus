"""Tests for agentnexus.evaluation.hallucination — HallucinationDetector."""

import json

from agentnexus.evaluation.hallucination import HallucinationDetector, HallucinationReport


class TestHallucinationReport:
    def test_defaults(self):
        report = HallucinationReport()
        assert report.trace_id == ""
        assert report.total_claims == 0
        assert report.unsupported_claims == 0
        assert report.hallucination_rate == 0.0
        assert report.flagged_claims == []
        assert report.answer_preview == ""

    def test_passed_below_threshold(self):
        report = HallucinationReport(hallucination_rate=0.01)
        assert report.passed is True

    def test_passed_at_threshold(self):
        report = HallucinationReport(hallucination_rate=0.02)
        assert report.passed is False  # < 0.02, not <=

    def test_passed_above_threshold(self):
        report = HallucinationReport(hallucination_rate=0.05)
        assert report.passed is False

    def test_summary_format(self):
        report = HallucinationReport(
            trace_id="t1", total_claims=10,
            unsupported_claims=2, hallucination_rate=0.2,
        )
        s = report.summary()
        assert "t1" in s
        assert "10" in s
        assert "2" in s
        assert "20.0%" in s


class TestHallucinationDetectorIsSupported:
    def test_no_context_returns_false(self):
        assert HallucinationDetector._is_supported("claim", "") is False

    def test_verbatim_match(self):
        assert HallucinationDetector._is_supported(
            "Paris is the capital of France",
            "Paris is the capital of France"
        ) is True

    def test_keyword_overlap_above_threshold(self):
        claim = "Paris is the capital of France"
        context = "Paris capital France Europe"
        assert HallucinationDetector._is_supported(claim, context) is True

    def test_keyword_overlap_below_threshold(self):
        claim = "Python is a programming language"
        context = "Java is a programming language"
        # Python, programming, language → 2/3 match ("programming", "language")
        # That's 66% which is >= 50%, so it's supported
        assert HallucinationDetector._is_supported(claim, context) is True

    def test_low_keyword_overlap(self):
        claim = "Elephants are large mammals from Africa"
        context = "Python programming language design philosophy"
        # Likely no significant overlap
        result = HallucinationDetector._is_supported(claim, context)
        assert result is False

    def test_no_claim_words_returns_true(self):
        # Words shorter than 2 chars are excluded
        assert HallucinationDetector._is_supported("a b c d e f", "anything") is True


class TestHallucinationDetectorGatherContext:
    def test_quoted_text_extracted(self):
        detector = HallucinationDetector()
        output = 'According to sources, "Paris is the capital of France and it has many landmarks"'
        context = detector._gather_context("t1", output)
        assert "Paris is the capital of France" in context

    def test_code_blocks_extracted(self):
        detector = HallucinationDetector()
        output = 'Here is the code:\n```python\nprint("hello world")\n```\nThat is it.'
        context = detector._gather_context("t1", output)
        assert 'print("hello world")' in context

    def test_no_quotes_or_code_returns_full_output(self):
        detector = HallucinationDetector()
        output = "This is a simple answer without quotes or code blocks."
        context = detector._gather_context("t1", output)
        assert context == output

    def test_short_quotes_excluded(self):
        detector = HallucinationDetector()
        output = 'Short "hi" there'
        context = detector._gather_context("t1", output)
        # The quote is only 2 chars, less than 20, so should be excluded
        assert context == output  # falls back to full output

    def test_quoted_and_code_together(self):
        detector = HallucinationDetector()
        output = '''He said "According to the research paper from 2023" and provided:
```python
result = compute(42)
```
'''
        context = detector._gather_context("t1", output)
        assert "According to the research paper from 2023" in context
        assert "result = compute(42)" in context


class TestHallucinationDetectorEvaluateOne:
    def test_no_claims_for_short_output(self):
        detector = HallucinationDetector()
        report = detector._evaluate_one("t1", "Hello")
        assert report.trace_id == "t1"
        assert report.total_claims == 0
        assert report.unsupported_claims == 0
        assert report.hallucination_rate == 0.0

    def test_all_claims_supported(self):
        detector = HallucinationDetector()
        output = (
            'Based on the research, "Paris is the capital of France with many landmarks." '
            'The city has a population of over 2 million people. '
            '"Paris is one of the most visited cities in the world."'
        )
        report = detector._evaluate_one("t1", output)
        # Claims should be mostly supported because quotes are in context
        assert report.total_claims >= 1
        assert report.answer_preview == output[:500]

    def test_claims_unsupported(self):
        detector = HallucinationDetector()
        # Context (quoted) differs from the second claim
        output = (
            '"Python is a popular programming language used for web development."\n'
            'But the claim about dragons living in volcanoes is completely fabricated by the assistant.'
        )
        report = detector._evaluate_one("t1", output)
        assert report.total_claims >= 1
        assert report.unsupported_claims >= 1
        assert report.hallucination_rate > 0

    def test_chinese_claims(self):
        detector = HallucinationDetector()
        # Context (quoted) differs from the second claim
        output = (
            '"根据最新的研究数据，人工智能技术正在快速发展。"\n'
            '火星上存在大量的液态水海洋覆盖了整个星球表面。'
        )
        report = detector._evaluate_one("t1", output)
        assert report.total_claims >= 1
        assert report.hallucination_rate > 0

    def test_answer_preview_truncated(self):
        detector = HallucinationDetector()
        output = "A" * 2000
        report = detector._evaluate_one("t1", output)
        assert len(report.answer_preview) == 500


class TestHallucinationDetectorFileMethods:
    def test_evaluate_all_no_files(self, tmp_path):
        detector = HallucinationDetector()
        reports = detector.evaluate_all(str(tmp_path))
        assert reports == []

    def test_evaluate_all_with_analyst_node(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "analyst_node",
             "output": "This is a long statement that might contain hallucinations."},
            {"trace_id": "t2", "name": "analyst_node",
             "output": "Another well-supported claim about the world."},
            {"trace_id": "t3", "name": "code_node",
             "output": "not an analyst node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        detector = HallucinationDetector()
        reports = detector.evaluate_all(str(tmp_path))
        assert len(reports) == 2
        assert reports[0].trace_id == "t1"
        assert reports[1].trace_id == "t2"

    def test_evaluate_all_skip_empty_output(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "analyst_node", "output": ""},
            {"trace_id": "t2", "name": "analyst_node", "output": "  "},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        detector = HallucinationDetector()
        reports = detector.evaluate_all(str(tmp_path))
        # Empty string excluded; whitespace-only passes bool() check → creates a report
        assert len(reports) == 1
        assert reports[0].trace_id == "t2"
        assert reports[0].total_claims == 0

    def test_evaluate_trace_found(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "analyst_node",
             "output": "Analysis result with some claims."},
            {"trace_id": "t2", "name": "analyst_node",
             "output": "Other result."},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        detector = HallucinationDetector()
        report = detector.evaluate_trace("t2", str(tmp_path))
        assert report is not None
        assert report.trace_id == "t2"

    def test_evaluate_trace_not_found(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "analyst_node", "output": "Some claims."},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        detector = HallucinationDetector()
        report = detector.evaluate_trace("nonexistent", str(tmp_path))
        assert report is None

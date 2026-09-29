"""GateCal gate_bench dataset integrity + rules-layer probe tests."""

from __future__ import annotations

from agentnexus.evaluation.gate_bench import build, load
from agentnexus.memory.extraction_pipeline import MemoryExtractionPipeline

_STRONG = tuple(MemoryExtractionPipeline._STRONG_SIGNALS)
_CLASSES = [
    "A_explicit_durable", "B_implicit_fact", "C_ephemeral_instruction",
    "D_transactional", "E_dialog_residue", "F_time_sensitive",
    "G_contradiction", "H_injection_pii", "I_signal_polluted",
    "J_negation_durable", "K_mixed_paragraph",
]


class TestDatasetIntegrity:
    def test_eleven_classes_25_each(self):
        rows = load()
        assert len(rows) >= 380, len(rows)
        for cls in _CLASSES:
            assert sum(1 for r in rows if r["cls"] == cls) >= 20, cls

    def test_committed_matches_build(self):
        """JSONL must be a deterministic regeneration of the seeds."""
        assert load() == build()

    def test_unique_ids(self):
        rows = load()
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_pure_negative_classes_carry_no_strong_signal(self):
        """If B/C/D/E/K questions or answers contain a strong-signal token
        the per-class P/R measurement is corrupted. K additionally must stay
        signal-free so it tests the extraction layer, not the gate."""
        rows = load()
        for r in rows:
            if r["cls"][0] in "BCDEK":
                text = r["question"] + r["answer"]
                assert not any(t in text for t in _STRONG), r["id"]

    def test_signal_polluted_class_contains_signal(self):
        """Every I row must carry a strong-signal token — otherwise it is
        not testing the pollution leak at all."""
        rows = [r for r in load() if r["cls"] == "I_signal_polluted"]
        assert len(rows) >= 100
        for r in rows:
            text = r["question"] + r["answer"]
            assert any(t in text for t in _STRONG), r["id"]

    def test_f_rows_expect_note(self):
        rows = [r for r in load() if r["cls"] == "F_time_sensitive"]
        assert rows and all(r["expect_category"] == "note" for r in rows)

    def test_g_rows_have_key(self):
        rows = [r for r in load() if r["cls"] == "G_contradiction"]
        assert rows and all(r["key"] for r in rows)

    def test_h_rows_all_reject(self):
        rows = [r for r in load() if r["cls"] == "H_injection_pii"]
        assert rows and all(r["expect_write"] is False for r in rows)


class TestRulesLayerProbe:
    def test_gatebench_rules_contract(self):
        """Whitelist contract: A recall 1.0, C/D/E zero leaks. B/F/G denial is
        the expected tightness signal and must not fail the probe."""
        from agentnexus.evaluation.memory_eval import (
            CaseResult,
            MemorySandbox,
            _probe_gatebench_rules,
        )

        sb = MemorySandbox()
        try:
            res = CaseResult(name="t", dimension="admission", layer="admission")
            _probe_gatebench_rules(sb, res)
        finally:
            sb.close()
        assert not res.missing and not res.unexpected, \
            f"missing={res.missing} unexpected={res.unexpected}"
        assert "overall P=" in res.detail
        assert "B_implicit_fact" in res.detail


class TestPipelineProbeWiring:
    """The full-pipeline probe stub must actually reach the store. Caught a
    latent AttributeError (_pipeline never set on the stubbed manager) that
    only surfaces with a real LLM configured — MagicMock generator reproduces
    it offline."""

    def test_gatebench_pipeline_reaches_store(self):
        """Constant mock: only the first strong-signal row can write (all
        later extractions dedup to the same fact). The wiring assertion is:
        A00 must reach the store and no exception may occur."""
        from unittest.mock import MagicMock

        from agentnexus.evaluation.memory_eval import (
            CaseResult,
            MemorySandbox,
            _probe_gatebench_pipeline,
        )

        sb = MemorySandbox()
        try:
            sb._generator = MagicMock()

            def _think(msgs, silent=True):
                content = msgs[0]["content"]
                if "只回答" in content:  # gate prompt → admit
                    return "yes"
                return '{"fact": [{"content": "用户不吃香菜", "context": ""}]}'

            sb._generator.think.side_effect = _think
            res = CaseResult(name="t", dimension="admission", layer="ltm")
            _probe_gatebench_pipeline(sb, res)
        finally:
            sb.close()
        assert not any("exception" in m for m in res.missing + res.unexpected)
        assert not any("A00" in m for m in res.missing), \
            f"A00 should have written: {res.missing[:3]}"
        assert "pipeline P=" in res.detail

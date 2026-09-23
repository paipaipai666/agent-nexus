"""Offline tests for public-benchmark converters (LoCoMo / LongMemEval).

Fixtures mirror the verified on-disk structures; no network.
"""

from __future__ import annotations

import json

import pytest

from agentnexus.eval.memory.convert import (
    ABSTAIN_GOLD,
    convert_locomo,
    convert_longmemeval,
    ensure_source,
    write_suite,
)
from agentnexus.eval.memory.dataset import load_suite


def _locomo_fixture() -> list[dict]:
    return [{
        "sample_id": "sample1",
        "conversation": {
            "speaker_a": "Caroline",
            "speaker_b": "Melanie",
            "session_1": [
                {"speaker": "Caroline", "dia_id": "D1:1", "text": "Hey Mel!"},
                {"speaker": "Melanie", "dia_id": "D1:2", "text": "Hi Caroline!"},
            ],
            "session_2": [
                {"speaker": "Caroline", "dia_id": "D2:1", "text": "I started yoga."},
            ],
            "session_2_date_time": "2023-06-01",
        },
        "qa": [
            {"question": "Who does yoga?", "answer": "Caroline",
             "evidence": ["D2:1"], "category": 4},                      # single-hop
            {"question": "When did Caroline greet Mel?", "answer": "May",
             "evidence": ["D1:1"], "category": 2},                      # temporal
            {"question": "What car does Caroline drive?", "answer": "",
             "evidence": [], "category": 5,
             "adversarial_answer": "a Tesla"},                          # adversarial
            {"question": "Likely career?", "answer": "Artist",
             "evidence": ["D1:1"], "category": 3},                      # open-domain
            {"question": "Greeting and yoga?", "answer": "Both",
             "evidence": ["D1:1", "D2:1"], "category": 1},              # multi-hop
        ],
    }]


def _longmemeval_fixture() -> list[dict]:
    return [
        {
            "question_id": "lme-1",
            "question": "What database do I use?",
            "question_type": "single-session-user",
            "answer": "PostgreSQL",
            "haystack_sessions": [[
                {"role": "user", "content": "I use PostgreSQL."},
                {"role": "assistant", "content": "Got it."},
            ]],
        },
        {
            "question_id": "lme-2",
            "question": "Which version now?",
            "question_type": "knowledge-update",
            "answer": "3.12",
            "answer_session_ids": ["session_9"],
            "haystack_sessions": [[
                {"role": "user", "content": "On Python 3.11."},
                {"role": "user", "content": "Upgraded to 3.12."},
            ]],
        },
        {
            "question_id": "lme-3",
            "question": "What is my server host?",
            "question_type": "abstention",
            "answer": "I don't know.",
            "haystack_sessions": [[
                {"role": "user", "content": "Random chat."},
            ]],
        },
        {
            "question_id": "lme-4",
            "question": "Unknown type question?",
            "question_type": "brand-new-type",
            "answer": "x",
            "haystack_sessions": [[{"role": "user", "content": "hi"}]],
        },
    ]


def test_convert_locomo_category_mapping():
    convs = convert_locomo(_locomo_fixture())
    assert len(convs) == 1
    conv = convs[0]
    # speaker_a → user, speaker_b → assistant, sessions in order
    assert [t.role for t in conv.turns] == ["user", "assistant", "user"]
    by_type = {q.qa_type: q for q in conv.questions}
    assert set(by_type) == {
        "single_hop", "temporal", "abstention", "open_domain", "multi_session",
    }
    assert by_type["abstention"].answer == ABSTAIN_GOLD   # trap answer discarded
    assert by_type["single_hop"].answer == "Caroline"
    assert by_type["single_hop"].evidence == ["D2:1"]     # provenance preserved


def test_convert_longmemeval_type_mapping():
    convs = convert_longmemeval(_longmemeval_fixture())
    # lme-4 has an unmapped question_type → dropped
    assert len(convs) == 3
    by_id = {c.id: c for c in convs}
    assert by_id["lme-1"].questions[0].qa_type == "single_hop"
    assert by_id["lme-2"].questions[0].qa_type == "knowledge_update"
    assert by_id["lme-2"].questions[0].evidence == ["session_9"]
    assert by_id["lme-3"].questions[0].answer == ABSTAIN_GOLD
    # user/assistant roles preserved, haystack flattened
    assert [t.role for t in by_id["lme-2"].turns] == ["user", "user"]


def test_write_suite_roundtrips_through_loader(tmp_path):
    convs = convert_locomo(_locomo_fixture()) + convert_longmemeval(_longmemeval_fixture())
    out = tmp_path / "suite.jsonl"
    write_suite(convs, out)
    loaded = load_suite(out)
    assert len(loaded) == len(convs)
    assert sum(len(c.questions) for c in loaded) == sum(len(c.questions) for c in convs)


def test_ensure_source_unknown():
    with pytest.raises(ValueError, match="unknown source"):
        ensure_source("nonsense", offline=True)


def test_ensure_source_offline_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_BENCHMARK_CACHE", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="Download"):
        ensure_source("locomo", offline=True)


def test_ensure_source_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_BENCHMARK_CACHE", str(tmp_path))
    cache = tmp_path / "memory"
    cache.mkdir(parents=True)
    (cache / "locomo10.json").write_text(
        # pad past the >1KB partial-download guard in ensure_source
        json.dumps(_locomo_fixture()) + " " * 1200, encoding="utf-8")
    path = ensure_source("locomo", offline=True)
    assert path.exists()

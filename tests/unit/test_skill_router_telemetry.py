"""Tests for route telemetry: event recording, feedback, and hard negatives."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from agentnexus.skills.router.telemetry import RouteEvent, RouteTelemetry


# ── Tests for RouteEvent ───────────────────────────────────────────


class TestRouteEvent:
    def test_default_values(self):
        event = RouteEvent()
        assert event.query == ""
        assert event.query_terms == ()
        assert event.selected_skill is None
        assert event.selected_score == 0.0
        assert event.selected_source == ""
        assert event.candidates == ()
        assert event.mode == "single"
        assert event.confidence == 0.0
        assert event.margin == 0.0
        assert event.accepted is None
        assert event.actual_skill is None
        assert isinstance(event.timestamp, float)

    def test_custom_values(self):
        event = RouteEvent(
            query="test query",
            query_terms=("test", "query"),
            selected_skill="default/test-skill",
            selected_score=4.5,
            selected_source="deterministic",
            candidates=({"id": "a"},),
            mode="ambiguous",
            confidence=0.7,
            margin=0.1,
            accepted=True,
            actual_skill="default/other",
        )
        assert event.query == "test query"
        assert event.query_terms == ("test", "query")
        assert event.selected_skill == "default/test-skill"
        assert event.selected_score == 4.5
        assert event.selected_source == "deterministic"
        assert event.candidates == ({"id": "a"},)
        assert event.mode == "ambiguous"
        assert event.confidence == 0.7
        assert event.margin == 0.1
        assert event.accepted is True
        assert event.actual_skill == "default/other"

    def test_json_serialization_via_asdict(self):
        event = RouteEvent(
            timestamp=1700000000.0,
            query="test",
            query_terms=("a", "b"),
            selected_skill="ns/skill",
            mode="single",
        )
        data = asdict(event)
        assert isinstance(data, dict)
        assert data["query"] == "test"
        assert data["query_terms"] == ("a", "b")
        assert data["selected_skill"] == "ns/skill"
        # Verify it's JSON-serializable
        json_str = json.dumps(data)
        restored = json.loads(json_str)
        assert restored["query"] == "test"


# ── Tests for RouteTelemetry ───────────────────────────────────────


class TestRouteTelemetry:
    def test_record_appends_to_memory(self):
        t = RouteTelemetry()
        event = RouteEvent(query="hello")
        t.record(event)
        assert len(t.events) == 1
        assert t.events[0].query == "hello"

    def test_record_writes_to_file_when_log_path_set(self, tmp_path):
        log_file = tmp_path / "events.jsonl"
        t = RouteTelemetry(log_path=str(log_file))
        event = RouteEvent(query="test-write")
        t.record(event)
        assert log_file.exists()
        line = log_file.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        assert data["query"] == "test-write"

    def test_record_oserror_no_exception_event_still_in_memory(self, mocker, tmp_path):
        log_file = tmp_path / "events.jsonl"
        t = RouteTelemetry(log_path=str(log_file))
        event = RouteEvent(query="will-fail-in-file")
        mocker.patch("builtins.open", side_effect=OSError("disk full"))
        # Should not raise
        t.record(event)
        assert len(t.events) == 1
        assert t.events[0].query == "will-fail-in-file"

    def test_record_feedback_updates_most_recent_unconfirmed(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="q1"))
        t.record(RouteEvent(query="q1"))
        t.record_feedback("q1", accepted=True, actual_skill="ns/correct")
        # Latest unconfirmed should be updated
        events = t.events
        assert events[1].accepted is True
        assert events[1].actual_skill == "ns/correct"
        # First should remain unchanged
        assert events[0].accepted is None

    def test_record_feedback_no_matching_query_no_change(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="q1"))
        t.record_feedback("nonexistent", accepted=False)
        assert t.events[0].accepted is None

    def test_record_feedback_multiple_same_query_updates_latest_unconfirmed(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="q1"))
        t.record(RouteEvent(query="q1"))
        t.record(RouteEvent(query="q1"))
        # Confirm the middle one manually
        t.events[1].accepted = False
        t.record_feedback("q1", accepted=True)
        # Should update the latest unconfirmed (index 2), not the already-confirmed one
        events = t.events
        assert events[0].accepted is None
        assert events[1].accepted is False
        assert events[2].accepted is True

    def test_record_feedback_with_actual_skill(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="find docs"))
        t.record_feedback("find docs", accepted=False, actual_skill="default/doc-search")
        assert t.events[0].accepted is False
        assert t.events[0].actual_skill == "default/doc-search"

    def test_events_property_returns_copy(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="q1"))
        events = t.events
        events.append(RouteEvent(query="injected"))
        # Internal list should be unaffected
        assert len(t.events) == 1

    def test_get_hard_negatives_returns_ambiguous_mode_events(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="ambiguous query", mode="ambiguous", selected_skill="ns/a"))
        negatives = t.get_hard_negatives()
        assert len(negatives) == 1
        assert negatives[0]["query"] == "ambiguous query"

    def test_get_hard_negatives_returns_accepted_false_events(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="wrong pick", mode="single", accepted=False))
        negatives = t.get_hard_negatives()
        assert len(negatives) == 1
        assert negatives[0]["query"] == "wrong pick"

    def test_get_hard_negatives_excludes_accepted_true(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="good pick", mode="single", accepted=True))
        negatives = t.get_hard_negatives()
        assert len(negatives) == 0

    def test_get_hard_negatives_excludes_accepted_none_non_ambiguous(self):
        t = RouteTelemetry()
        t.record(RouteEvent(query="unconfirmed", mode="single", accepted=None))
        negatives = t.get_hard_negatives()
        assert len(negatives) == 0

    def test_get_hard_negatives_correct_dict_keys(self):
        t = RouteTelemetry()
        t.record(RouteEvent(
            query="q",
            selected_skill="ns/s",
            actual_skill="ns/a",
            candidates=({"id": "c"},),
            margin=0.3,
            mode="ambiguous",
        ))
        negatives = t.get_hard_negatives()
        assert len(negatives) == 1
        neg = negatives[0]
        assert set(neg.keys()) == {"query", "selected", "actual", "candidates", "margin"}
        assert neg["query"] == "q"
        assert neg["selected"] == "ns/s"
        assert neg["actual"] == "ns/a"
        assert neg["candidates"] == ({"id": "c"},)
        assert neg["margin"] == 0.3

    def test_empty_events_get_hard_negatives_returns_empty(self):
        t = RouteTelemetry()
        assert t.get_hard_negatives() == []

    def test_log_path_as_path_object(self, tmp_path):
        log_file = tmp_path / "path_obj.jsonl"
        t = RouteTelemetry(log_path=log_file)
        t.record(RouteEvent(query="path-test"))
        assert log_file.exists()
        data = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert data["query"] == "path-test"

    def test_log_path_as_string(self, tmp_path):
        log_file = tmp_path / "str_path.jsonl"
        t = RouteTelemetry(log_path=str(log_file))
        t.record(RouteEvent(query="str-test"))
        assert log_file.exists()
        data = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert data["query"] == "str-test"

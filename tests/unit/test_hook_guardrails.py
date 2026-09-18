"""Guardrail tests for the hook system (M3).

Covers:
- read-only payload mutation detection (non-_MUTABLE_HOOKS warn + name the hook)
- exception surfacing (warning once per hook name, then debug-counted)
- payload schema validation (fire-site drift logs an error; gated by config)
- HookContext.to_feedback() standardized format
"""

from __future__ import annotations

import logging

from agentnexus.core.config import get_settings
from agentnexus.core.hooks import (
    PAYLOAD_SCHEMAS,
    HookContext,
    HookManager,
    HookType,
)


class TestMutationGuard:
    def test_readonly_payload_mutation_warns(self, caplog):
        mgr = HookManager()

        def bad_hook(ctx):
            ctx.payload["result"] = "tampered"

        mgr.register(HookType.AFTER_TOOL_CALL, bad_hook, name="bad")
        with caplog.at_level(logging.WARNING):
            mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": "ok"})
        assert any(
            "bad" in rec.message and "mutated read-only" in rec.message
            for rec in caplog.records
        )

    def test_mutable_payload_change_is_silent(self, caplog):
        mgr = HookManager()

        def ok_hook(ctx):
            ctx.payload["approved"] = True

        mgr.register(HookType.BEFORE_TOOL_CALL, ok_hook, name="ok")
        with caplog.at_level(logging.WARNING):
            ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "x", "params": {}})
        assert ctx.payload["approved"] is True
        assert not any("mutated read-only" in r.message for r in caplog.records)


class TestExceptionSurfacing:
    def test_first_exception_warns_subsequent_debug(self, caplog):
        mgr = HookManager()

        def flaky(ctx):
            raise RuntimeError("boom")

        mgr.register(HookType.AFTER_TOOL_CALL, flaky, name="flaky")
        with caplog.at_level(logging.DEBUG):
            mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": ""})
            entry = mgr._hooks["flaky"]
            assert entry.fail_count == 1
            # second fire: no new warning, count grows
            caplog.clear()
            mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": ""})
        assert entry.fail_count == 2
        assert not any("flaky" in r.message and r.levelno >= logging.WARNING
                       for r in caplog.records)
        assert any("flaky" in r.message for r in caplog.records)  # debug line exists


class TestPayloadSchemaValidation:
    def test_missing_key_logs_error(self, caplog):
        mgr = HookManager()
        with caplog.at_level(logging.ERROR):
            mgr.fire(HookType.AFTER_TOOL_CALL, {"params": {}, "result": "ok"})  # no "name"
        assert any("missing key" in r.message for r in caplog.records)

    def test_wrong_type_logs_error(self, caplog):
        mgr = HookManager()
        with caplog.at_level(logging.ERROR):
            mgr.fire(HookType.AGENT_START, {"question": ["not", "str"], "agent_id": "a"})
        assert any("expected" in r.message for r in caplog.records)

    def test_schema_check_disabled(self, monkeypatch, caplog):
        monkeypatch.setattr(get_settings(), "hook_schema_check", False, raising=False)
        mgr = HookManager()
        with caplog.at_level(logging.ERROR):
            mgr.fire(HookType.AGENT_START, {})  # missing both keys
        assert not any("missing key" in r.message for r in caplog.records)

    def test_all_hook_types_have_schema(self):
        for hook_type in HookType:
            assert hook_type in PAYLOAD_SCHEMAS, f"{hook_type.value} missing schema"


class TestAbortFeedback:
    def test_to_feedback_format(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort("no .env edits", code="POLICY_VIOLATION", details={"path": ".env"})
        text = ctx.to_feedback()
        assert text.startswith("[hook blocked] POLICY_VIOLATION")
        assert "no .env edits" in text
        assert ".env" in text

    def test_to_feedback_minimal(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort("plain reason")
        assert ctx.to_feedback() == "[hook blocked] BLOCKED: plain reason"

"""Tests for agentnexus.tools.confirm_bridge."""

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from agentnexus.tools.confirm_bridge import ConfirmBridge


class TestConfirmBridge:
    def test_default_returns_false(self):
        bridge = ConfirmBridge()
        assert bridge("anything") is False

    def test_set_target_none_returns_false(self):
        bridge = ConfirmBridge()
        bridge.set_target(None)
        assert bridge("anything") is False

    def test_set_target_callable_returns_result(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: True)
        assert bridge("summary") is True

    def test_set_target_callable_returns_false(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: False)
        assert bridge("summary") is False

    def test_set_target_replaces_previous(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: False)
        bridge.set_target(lambda s: True)
        assert bridge("summary") is True

    def test_callable_receives_summary(self):
        bridge = ConfirmBridge()
        mock = MagicMock(return_value=True)
        bridge.set_target(mock)
        bridge("test summary")
        mock.assert_called_once_with("test summary")

    def test_callable_return_value_is_bool_coerced(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda s: 1)
        assert bridge("x") is True

        bridge.set_target(lambda s: 0)
        assert bridge("x") is False

        bridge.set_target(lambda s: "")
        assert bridge("x") is False

    def test_thread_target_overrides_global_target_for_that_thread(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda _s: False)
        bridge.set_target(lambda _s: True, thread_id=threading.get_ident())

        assert bridge("x") is True

        bridge.set_target(None, thread_id=threading.get_ident())
        assert bridge("x") is False

    def test_thread_targets_are_isolated(self):
        bridge = ConfirmBridge()
        bridge.set_target(lambda _s: False)
        result_holder = []

        def call_with_thread_target():
            bridge.set_target(lambda _s: True, thread_id=threading.get_ident())
            try:
                result_holder.append(bridge("x"))
            finally:
                bridge.set_target(None, thread_id=threading.get_ident())

        thread = threading.Thread(target=call_with_thread_target)
        thread.start()
        thread.join(timeout=1)

        assert result_holder == [True]
        assert bridge("x") is False

    def test_inherited_run_target_receives_worker_confirmation(self):
        bridge = ConfirmBridge()
        summaries = []
        bridge.set_target(lambda summary: summaries.append(summary) or True, thread_id=threading.get_ident())
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                approved = pool.submit(contextvars.copy_context().run, bridge, "outside file request").result(timeout=2)
            assert approved is True
            assert summaries == ["outside file request"]
        finally:
            bridge.set_target(None, thread_id=threading.get_ident())

    @pytest.mark.parametrize("replace_target", [False, True])
    def test_inherited_target_is_revoked_when_run_ends(self, replace_target):
        bridge = ConfirmBridge()
        previous = MagicMock(return_value=True)
        replacement = MagicMock(return_value=True)
        bridge.set_target(previous, thread_id=threading.get_ident())
        inherited = contextvars.copy_context()
        bridge.set_target(None, thread_id=threading.get_ident())
        if replace_target:
            bridge.set_target(replacement, thread_id=threading.get_ident())
        else:
            bridge.set_target(replacement)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(inherited.run, bridge, "late request").result(timeout=2) is False
            previous.assert_not_called()
            replacement.assert_not_called()
        finally:
            bridge.set_target(None, thread_id=threading.get_ident())
            bridge.set_target(None)

    def test_approval_is_discarded_when_run_ends_while_waiting(self):
        bridge = ConfirmBridge()
        entered = threading.Event()
        release = threading.Event()

        def approve(_summary):
            entered.set()
            assert release.wait(timeout=2)
            return True

        bridge.set_target(approve, thread_id=threading.get_ident())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(contextvars.copy_context().run, bridge, "outside request")
            try:
                assert entered.wait(timeout=2)
                bridge.set_target(None, thread_id=threading.get_ident())
            finally:
                release.set()
            assert pending.result(timeout=2) is False

    def test_parallel_requests_to_same_run_are_serialized(self):
        bridge = ConfirmBridge()
        first_entered = threading.Event()
        second_started = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()
        summaries = []

        def approve(summary):
            summaries.append(summary)
            if summary == "first":
                first_entered.set()
                assert release.wait(timeout=2)
                return True
            second_entered.set()
            return False

        def request_second():
            second_started.set()
            return bridge("second")

        bridge.set_target(approve, thread_id=threading.get_ident())
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(contextvars.copy_context().run, bridge, "first")
                assert first_entered.wait(timeout=2)
                second = pool.submit(contextvars.copy_context().run, request_second)
                try:
                    assert second_started.wait(timeout=2)
                    assert not second_entered.wait(timeout=0.1)
                finally:
                    release.set()
                assert first.result(timeout=2) is True
                assert second.result(timeout=2) is False
            assert summaries == ["first", "second"]
        finally:
            bridge.set_target(None, thread_id=threading.get_ident())

    def test_different_run_targets_can_confirm_concurrently(self):
        bridge = ConfirmBridge()
        both_entered = threading.Barrier(2)

        def run(approved):
            def confirm(_summary):
                both_entered.wait(timeout=2)
                return approved

            bridge.set_target(confirm, thread_id=threading.get_ident())
            try:
                with ThreadPoolExecutor(max_workers=1) as child_pool:
                    return child_pool.submit(contextvars.copy_context().run, bridge, "request").result(timeout=3)
            finally:
                bridge.set_target(None, thread_id=threading.get_ident())

        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = pool.submit(run, True)
            rejected = pool.submit(run, False)
            assert accepted.result(timeout=4) is True
            assert rejected.result(timeout=4) is False

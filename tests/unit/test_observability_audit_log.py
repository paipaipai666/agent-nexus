import threading

import pytest

from agentnexus.observability.audit_log import (
    ThreadSafeAuditLog,
    _global_audit_log,
    append_audit,
    get_audit_log,
)


@pytest.fixture(autouse=True)
def _clear_global_audit_log():
    _global_audit_log.clear()
    yield
    _global_audit_log.clear()


class TestThreadSafeAuditLogAppend:

    def test_append_increments_length(self):
        log = ThreadSafeAuditLog()
        assert len(log) == 0
        log.append({"action": "test"})
        assert len(log) == 1
        log.append({"action": "test2"})
        assert len(log) == 2


class TestThreadSafeAuditLogCopy:

    def test_copy_returns_snapshot(self):
        log = ThreadSafeAuditLog()
        log.append({"a": 1})
        log.append({"a": 2})
        snapshot = log.copy()
        assert len(snapshot) == 2
        log.append({"a": 3})
        assert len(snapshot) == 2


class TestThreadSafeAuditLogClear:

    def test_clear_empties_log(self):
        log = ThreadSafeAuditLog()
        log.append({"a": 1})
        log.append({"a": 2})
        assert len(log) == 2
        log.clear()
        assert len(log) == 0


class TestThreadSafeAuditLogIter:

    def test_iter_iterates_over_copy(self):
        log = ThreadSafeAuditLog()
        log.append({"x": 10})
        log.append({"x": 20})
        items = list(log)
        assert items == [{"x": 10}, {"x": 20}]

    def test_iter_does_not_affect_original(self):
        log = ThreadSafeAuditLog()
        log.append({"x": 1})
        for _ in log:
            pass
        assert len(log) == 1


class TestThreadSafeAuditLogGetItem:

    def test_getitem_int(self):
        log = ThreadSafeAuditLog()
        log.append({"a": "first"})
        log.append({"a": "second"})
        assert log[0] == {"a": "first"}
        assert log[1] == {"a": "second"}

    def test_getitem_negative_index(self):
        log = ThreadSafeAuditLog()
        log.append({"a": 1})
        log.append({"a": 2})
        assert log[-1] == {"a": 2}

    def test_getitem_slice(self):
        log = ThreadSafeAuditLog()
        for i in range(5):
            log.append({"i": i})
        result = log[1:3]
        assert result == [{"i": 1}, {"i": 2}]
        assert isinstance(result, list)


class TestThreadSafeAuditLogThreadSafety:

    def test_concurrent_appends(self):
        log = ThreadSafeAuditLog()
        n_threads = 8
        per_thread = 100

        def worker(tid):
            for i in range(per_thread):
                log.append({"tid": tid, "i": i})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(log) == n_threads * per_thread


class TestGetAuditLog:

    def test_returns_copy(self):
        append_audit({"global": True})
        snap1 = get_audit_log()
        snap2 = get_audit_log()
        assert snap1 == snap2
        assert snap1 is not snap2


class TestAppendAudit:

    def test_appends_to_global_log(self):
        before = len(get_audit_log())
        append_audit({"action": "unit_test_entry"})
        after = get_audit_log()
        assert len(after) == before + 1
        assert after[-1] == {"action": "unit_test_entry"}

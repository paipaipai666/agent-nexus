"""Optional observation sinks selected by the current application owner."""

from __future__ import annotations

import asyncio
import functools
import logging
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol

from agentnexus.core.modules import get_module_host

logger = logging.getLogger(__name__)


def _observe(operation: Callable, default=None):
    try:
        return operation()
    except Exception:
        logger.debug("Observation operation failed", exc_info=True)
        return default


class Span(Protocol):
    """Mutable observation handle returned by a selected sink."""

    output: dict[str, Any]
    metadata: dict[str, Any]


class TraceBackend(Protocol):
    """Runtime-owned observation implementation independent of its callers."""

    active: Any | None
    audit_log: Any

    def span(self, name: str, input_data: dict | None = None) -> AbstractContextManager[Span]: ...
    def start_trace(self, task: str, metadata: dict | None = None) -> Any: ...
    def end_trace(self) -> None: ...
    def set_inherited_trace(self, trace_id: str | None) -> None: ...
    def get_inherited_trace(self) -> str | None: ...
    def create_drift_detector(self, original_goal: str, max_steps: int) -> Any: ...
    def emit_drift_alerts(self, signals: list, *, trace_id: str = "") -> list: ...
    def stats(self, days: int = 7) -> Any: ...
    def evaluate_stats(self, stats: Any) -> list: ...


class _DiscardedSpan:
    __slots__ = ()

    @property
    def output(self) -> dict:
        return {}

    @output.setter
    def output(self, value: dict) -> None:
        pass

    @property
    def metadata(self) -> dict:
        return {}

    @metadata.setter
    def metadata(self, value: dict) -> None:
        pass


_DISCARDED_SPAN = _DiscardedSpan()


@contextmanager
def _span(backend, name: str, input_data: dict | None = None):
    if backend is None:
        yield _DISCARDED_SPAN
        return
    try:
        scope = backend.span(name, input_data)
        handle = scope.__enter__()
    except Exception:
        logger.debug("Observation span entry failed", exc_info=True)
        yield _DISCARDED_SPAN
        return
    try:
        yield handle
    except BaseException:
        error = sys.exc_info()
        _observe(lambda: scope.__exit__(*error))
        raise
    else:
        _observe(lambda: scope.__exit__(None, None, None))


class TraceFacade:
    """Resolve the selected sink at each call without loading default telemetry."""

    @property
    def active(self):
        backend = get_module_host().optional("observability")
        return _observe(lambda: backend.active) if backend is not None else None

    def start_trace(self, task: str, metadata: dict | None = None):
        backend = get_module_host().optional("observability")
        return _observe(lambda: backend.start_trace(task, metadata)) if backend is not None else None

    def end_trace(self) -> None:
        backend = get_module_host().optional("observability")
        if backend is not None:
            _observe(backend.end_trace)

    def start_span(self, name: str, input_data: dict | None = None):
        context = self.active
        return _observe(lambda: context.start_span(name, input_data)) if context is not None else None

    def end_span(self, span, output_data: dict | None = None, metadata: dict | None = None) -> None:
        context = self.active
        if context is not None:
            _observe(lambda: context.end_span(span, output_data=output_data, metadata=metadata))

    def span(self, name: str, input_data: dict | None = None):
        backend = get_module_host().optional("observability")
        return _span(backend, name, input_data)

    def set_inherited_trace(self, trace_id: str | None) -> None:
        backend = get_module_host().optional("observability")
        if backend is not None:
            _observe(lambda: backend.set_inherited_trace(trace_id))

    def get_inherited_trace(self) -> str | None:
        backend = get_module_host().optional("observability")
        return _observe(backend.get_inherited_trace) if backend is not None else None

    def configure(self, traces_dir: str) -> None:
        """Configure an explicitly selected sink for standalone operations."""
        backend = get_module_host().optional("observability")
        if backend is not None:
            _observe(lambda: backend.configure(traces_dir))


trace_manager = TraceFacade()


def trace_span(name: str, attrs: dict | Callable[..., dict] | None = None):
    """Observe a call only when its current application selected a sink."""
    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                backend = get_module_host().optional("observability")
                if backend is None:
                    return await fn(*args, **kwargs)
                data = _observe(lambda: attrs(*args, **kwargs)) if callable(attrs) else attrs
                with _span(backend, name, data):
                    return await fn(*args, **kwargs)
            return async_wrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            backend = get_module_host().optional("observability")
            if backend is None:
                return fn(*args, **kwargs)
            data = _observe(lambda: attrs(*args, **kwargs)) if callable(attrs) else attrs
            with _span(backend, name, data):
                return fn(*args, **kwargs)
        return wrapper
    return decorator


class AuditLog:
    """Write and inspect audit observations through the selected sink."""

    def append(self, entry: Any) -> None:
        append_audit(entry)

    def copy(self) -> list:
        return get_audit_log()

    def clear(self) -> None:
        backend = get_module_host().optional("observability")
        if backend is not None:
            _observe(lambda: backend.audit_log.clear())

    def __iter__(self) -> Iterator:
        return iter(self.copy())

    def __len__(self) -> int:
        return len(self.copy())


def append_audit(entry: Any) -> None:
    """Send a tool audit entry without retaining it when observation is absent."""
    backend = get_module_host().optional("observability")
    if backend is not None:
        _observe(lambda: backend.audit_log.append(entry))


def get_audit_log() -> list:
    """Return audit observations belonging to the current runtime."""
    backend = get_module_host().optional("observability")
    return _observe(lambda: list(backend.audit_log), []) if backend is not None else []


class _Drift:
    def __init__(self, original_goal: str, detector=None):
        self.original_goal = original_goal
        self._detector = detector

    def check(self, **kwargs) -> list:
        return _observe(lambda: self._detector.check(**kwargs), []) if self._detector is not None else []

    def record_step(self, **kwargs) -> None:
        if self._detector is not None:
            _observe(lambda: self._detector.record_step(**kwargs))


def create_drift_detector(original_goal: str, max_steps: int):
    """Construct drift observation only through the selected implementation."""
    backend = get_module_host().optional("observability")
    detector = _observe(lambda: backend.create_drift_detector(original_goal, max_steps)) if backend is not None else None
    return _Drift(original_goal, detector)


def emit_drift_alerts(signals: list, *, trace_id: str = "") -> list:
    """Send drift observations to the current runtime's alert sink."""
    backend = get_module_host().optional("observability")
    return _observe(lambda: backend.emit_drift_alerts(signals, trace_id=trace_id), []) if backend is not None else []


def evaluate_run_alerts() -> None:
    """Evaluate run statistics only when observation is available."""
    backend = get_module_host().optional("observability")
    if backend is not None:
        _observe(lambda: backend.evaluate_stats(backend.stats(days=1)))


def span_record(trace_id: str, span: Any) -> dict:
    """Serialize the public trace fields for evaluation consumers."""
    return {
        "trace_id": trace_id, "span_id": span.span_id,
        "parent_span_id": span.parent_span_id, "name": span.name,
        "start_time": span.start_time, "end_time": span.end_time,
        "latency_ms": span.latency_ms, "input": span.input,
        "output": span.output, "metadata": span.metadata,
    }

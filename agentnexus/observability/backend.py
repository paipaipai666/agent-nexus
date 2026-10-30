"""Built-in observation implementation owned by one application runtime."""

from __future__ import annotations

from agentnexus.observability.alerting import AlertManager, emit_drift_alerts, evaluate_stats, setup_default_alerts
from agentnexus.observability.audit_log import ThreadSafeAuditLog
from agentnexus.observability.drift_detector import DriftDetector
from agentnexus.observability.inspection import read_trace_spans, trace_spans
from agentnexus.observability.stats import compute_stats
from agentnexus.observability.tracer import TraceManager


class ObservabilityBackend:
    """Own tracing, audit storage and alert history without process-wide state."""

    def __init__(self, settings):
        self.settings = settings
        self.traces_dir = getattr(settings, "traces_dir", "")
        self.tracer = TraceManager()
        self.tracer.configure(self.traces_dir)
        self.tracer.retention_days = getattr(settings, "trace_retention_days", 30)
        self.audit_log = ThreadSafeAuditLog()
        self.alert_manager = setup_default_alerts(self.traces_dir, manager=AlertManager())

    @property
    def active(self):
        return self.tracer.active

    def configure(self, traces_dir: str) -> None:
        self.traces_dir = traces_dir
        self.tracer.configure(traces_dir)

    def health(self) -> dict:
        """Verify this backend's own trace storage is writable."""
        from pathlib import Path
        from tempfile import NamedTemporaryFile

        directory = Path(self.traces_dir)
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=directory, prefix=".health-check-"):
            pass
        return {"status": "ok", "path": str(directory)}

    def start_trace(self, task: str, metadata: dict | None = None):
        return self.tracer.start_trace(task, metadata)

    def end_trace(self) -> None:
        self.tracer.end_trace()

    def span(self, name: str, input_data: dict | None = None):
        return self.tracer.span(name, input_data)

    def set_inherited_trace(self, trace_id: str | None) -> None:
        self.tracer.set_inherited_trace(trace_id)

    def get_inherited_trace(self) -> str | None:
        return self.tracer.get_inherited_trace()

    def create_drift_detector(self, original_goal: str, max_steps: int):
        return DriftDetector(original_goal, max_steps)

    def emit_drift_alerts(self, signals: list, *, trace_id: str = "") -> list:
        return emit_drift_alerts(signals, trace_id=trace_id, manager=self.alert_manager)

    def stats(self, days: int = 7):
        return compute_stats(self.traces_dir, days)

    def evaluate_stats(self, stats) -> list:
        return evaluate_stats(stats, manager=self.alert_manager)

    def trace_spans(self, days: int | None = None):
        return trace_spans(self.traces_dir, days)

    def read_trace_spans(self, days: int | None = None) -> list[dict]:
        return read_trace_spans(self.traces_dir, days)

    def list_logs(self, days: int = 7) -> dict:
        grouped = {}
        for day, span in self.trace_spans(days):
            trace_id = span.get("trace_id", "")
            entry = grouped.setdefault(trace_id, {"trace_id": trace_id, "date": day, "span_count": 0, "spans": []})
            entry["spans"].append(span)
            entry["span_count"] += 1
        return {"traces": list(grouped.values())[:50], "total": len(grouped)}

    def get_trace(self, trace_id: str) -> dict:
        return {"trace_id": trace_id, "spans": [span for span in self.read_trace_spans() if span.get("trace_id") == trace_id]}

    def close(self) -> None:
        self.tracer.end_trace()

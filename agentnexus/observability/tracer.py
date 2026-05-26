"""结构化 Trace 系统 — 核心追踪模块

每个 Agent 节点执行时创建 Span，记录输入/输出/延迟/token 消耗，
最终以 JSONL 格式写入 ~/.agentnexus/traces/ 目录。
"""

import atexit
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ── Span ────────────────────────────────────────────────────────────

@dataclass
class TraceSpan:
    """一次操作（LLM 调用 / Agent 节点 / 工具执行）的追踪记录"""
    span_id: str
    parent_span_id: str = ""
    name: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    _flushed: bool = False  # True if already written to disk (crash-safe)

    @property
    def latency_ms(self) -> float:
        return round((self.end_time - self.start_time) * 1000, 2)

    @property
    def status(self) -> str:
        return self.metadata.get("status", "ok")


# ── TraceContext ─────────────────────────────────────────────────────

class TraceContext:
    """鍗曟浠诲姟鐨勫畬鏁?trace锛屽寘鍚墍鏈?span"""

    def __init__(
        self,
        trace_id: Optional[str] = None,
        on_span_end: Callable[["TraceContext", TraceSpan], None] | None = None,
    ):
        self.trace_id = trace_id or str(uuid.uuid4())[:8]
        self.spans: list[TraceSpan] = []
        self._span_stack: list[TraceSpan] = []
        self._lock = threading.RLock()
        self._on_span_end = on_span_end

    def start_span(self, name: str, input_data: Optional[dict] = None) -> TraceSpan:
        """鍒涘缓瀛?span 骞舵帹鍏ユ爤椤?"""
        with self._lock:
            parent_id = self._span_stack[-1].span_id if self._span_stack else ""
            span = TraceSpan(
                span_id=str(uuid.uuid4())[:8],
                parent_span_id=parent_id,
                name=name,
                start_time=time.time(),
                input=input_data or {},
            )
            self._span_stack.append(span)
            return span

    def end_span(self, span: TraceSpan, output_data: Optional[dict] = None,
                 metadata: Optional[dict] = None):
        """缁撴潫 span 骞惰褰曞埌 spans 鍒楄〃"""
        should_flush = False
        with self._lock:
            if span in self.spans:
                return
            span.end_time = time.time()
            if output_data:
                span.output = _truncate_dict(output_data)
            if metadata:
                span.metadata = metadata
            self.spans.append(span)
            should_flush = True

            # 浠庢爤涓Щ闄わ紙淇濇寔 LIFO 寮瑰嚭锛?
            if self._span_stack and self._span_stack[-1].span_id == span.span_id:
                self._span_stack.pop()
            else:
                self._span_stack = [s for s in self._span_stack if s.span_id != span.span_id]
        if should_flush and self._on_span_end:
            self._on_span_end(self, span)


# ── TraceManager (Singleton, Thread-Safe) ────────────────────────────

class TraceManager:
    """管理 trace 上下文的单例管理器"""

    _instance: Optional["TraceManager"] = None
    _lock = threading.Lock()
    _local = threading.local()
    _traces_dir: str = ""

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def configure(cls, traces_dir: str):
        """设置 trace 文件输出目录"""
        cls._traces_dir = traces_dir

    @property
    def active(self) -> Optional[TraceContext]:
        return getattr(self._local, "trace", None)

    def start_trace(self, task: str) -> TraceContext:
        """开始一次新 trace"""
        ctx = TraceContext(on_span_end=self._flush_span)
        root_span = ctx.start_span("task", {"task": _truncate(task)})
        # 根 span 不入栈，作为隐式上下文
        ctx._span_stack.clear()
        ctx._span_stack.append(root_span)
        self._local.trace = ctx
        return ctx

    def end_trace(self):
        """结束当前 trace 并写入 JSONL"""
        ctx = self.active
        if ctx is None:
            return
        # 结束根 span
        if ctx._span_stack:
            root = ctx._span_stack[-1]
            ctx.end_span(root)
        self._flush(ctx)
        self._local.trace = None

    def span(self, name: str, input_data: Optional[dict] = None):
        """获取一个上下文管理器来包裹 span"""
        return _SpanContext(self, name, input_data)

    def _flush(self, ctx: TraceContext):
        if not self._traces_dir:
            return
        date_str = time.strftime("%Y-%m-%d")
        traces_path = Path(self._traces_dir)
        try:
            traces_path.mkdir(parents=True, exist_ok=True)
            file_path = traces_path / f"{date_str}.jsonl"

            with open(file_path, "a", encoding="utf-8") as f:
                for span in ctx.spans:
                    if span._flushed:
                        continue  # already written by _flush_span
                    record = self._span_record(ctx.trace_id, span)
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    span._flushed = True

            self._cleanup_old_traces()
        except OSError:
            return

    def _flush_span(self, ctx: TraceContext, span: TraceSpan):
        """Write a single span to disk immediately (crash-safe)."""
        if not self._traces_dir or span._flushed:
            return
        date_str = time.strftime("%Y-%m-%d")
        file_path = Path(self._traces_dir) / f"{date_str}.jsonl"
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            record = self._span_record(ctx.trace_id, span)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            span._flushed = True
        except OSError:
            return

    @staticmethod
    def _span_record(trace_id: str, span: TraceSpan) -> dict:
        return {
            "trace_id": trace_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "name": span.name,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "latency_ms": span.latency_ms,
            "input": span.input,
            "output": span.output,
            "metadata": span.metadata,
        }

    def _cleanup_old_traces(self):
        """Remove JSONL files older than the configured retention period."""
        try:
            from agentnexus.core.config import get_settings
            retention_days = get_settings().trace_retention_days
        except Exception:
            retention_days = 30

        cutoff = time.time() - retention_days * 86400
        traces_dir = Path(self._traces_dir)
        for f in traces_dir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


# ── Span Context Manager ─────────────────────────────────────────────

class _SpanContext:
    """用 with 语句管理 span 生命周期"""

    def __init__(self, manager: TraceManager, name: str, input_data: Optional[dict]):
        self._manager = manager
        self._name = name
        self._input = input_data
        self.span: Optional[TraceSpan] = None

    def __enter__(self) -> TraceSpan:
        ctx = self._manager.active
        if ctx is None:
            # 无 trace 时仍然创建 span（但不关联 trace）
            span_id = str(uuid.uuid4())[:8]
            self.span = TraceSpan(span_id=span_id, name=self._name,
                                  start_time=time.time(), input=self._input or {})
            return self.span
        self.span = ctx.start_span(self._name, self._input)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.span is None:
            return
        md = {"status": "error" if exc_type else "ok"}
        if exc_type:
            md["error"] = str(exc_val)
        ctx = self._manager.active
        if ctx is None:
            self.span.end_time = time.time()
            self.span.metadata = md
            return
        ctx.end_span(self.span, metadata=md)
        return False  # 不吞掉异常


# ── Helpers ──────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int = 5000) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...[截断 {len(text) - max_len} 字符]"


def _truncate_dict(d: dict[str, Any], max_len: int = 5000) -> dict[str, Any]:
    return {k: _truncate(str(v), max_len) for k, v in d.items()}


# ── 全局实例 ─────────────────────────────────────────────────────────

trace_manager = TraceManager()


@atexit.register
def _flush_on_exit():
    ctx = trace_manager.active
    if ctx is not None and ctx.spans:
        trace_manager.end_trace()

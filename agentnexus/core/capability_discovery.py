"""Cached capability discovery shared by clients using the same model configuration."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentnexus.core.config import get_config_dir

logger = logging.getLogger(__name__)

SUCCESS_TTL_SECONDS = 86_400
FAILURE_BACKOFF_SECONDS = 30
PROBE_BUDGET_SECONDS = 5.0


@dataclass(frozen=True)
class DiscoverySnapshot:
    """The last completed discovery or its current progress."""

    status: Literal["unknown", "probing", "ready", "failed"] = "unknown"
    result: dict[str, bool] | None = None
    expires_at: float = 0


class CapabilityDiscovery:
    """Deduplicate one configuration's probes and persist successful results."""

    _lock = threading.RLock()
    _records: dict[Path, DiscoverySnapshot] = {}
    _pending: dict[Path, Future[DiscoverySnapshot]] = {}

    def __init__(self, model: str, base_url: str, api_key: str, configuration: dict):
        identity = json.dumps([model, base_url.rstrip("/"), api_key, configuration], sort_keys=True)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        self.path = get_config_dir() / "capability-cache" / f"{digest}.json"

    def snapshot(self) -> DiscoverySnapshot:
        """Read a local cache entry without starting or waiting for a probe."""
        with self._lock:
            if self.path in self._pending:
                return DiscoverySnapshot(status="probing")
            cached = self._records.get(self.path)
            if cached is not None and cached.expires_at > time.time():
                return cached
            cached = self._read()
            if cached.status == "ready":
                self._records[self.path] = cached
            return cached

    def _read(self) -> DiscoverySnapshot:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                return DiscoverySnapshot()
            expiry = data["expires_at"]
            result = data["result"]
            keys = {"tool_calling", "json_mode", "parallel_tool_calls"}
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
                return DiscoverySnapshot()
            if not isinstance(result, dict) or result.keys() != keys or any(type(value) is not bool for value in result.values()):
                return DiscoverySnapshot()
            return DiscoverySnapshot("ready", result, expiry)
        except (OSError, ValueError, KeyError, TypeError):
            return DiscoverySnapshot()

    def _write(self, snapshot: DiscoverySnapshot) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"version": 1, "expires_at": snapshot.expires_at, "result": snapshot.result}, output)
            os.replace(temporary, self.path)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def _begin(self) -> tuple[Future[DiscoverySnapshot], bool]:
        with self._lock:
            snapshot = self.snapshot()
            if snapshot.status in {"ready", "failed"}:
                done = Future()
                done.set_result(snapshot)
                return done, False
            if self.path in self._pending:
                return self._pending[self.path], False
            pending = Future()
            self._pending[self.path] = pending
            return pending, True

    def _run(self, pending: Future[DiscoverySnapshot], loader: Callable[[], dict[str, bool] | None]) -> None:
        try:
            result = loader()
        except Exception:
            result = None
        snapshot = DiscoverySnapshot("ready", result, time.time() + SUCCESS_TTL_SECONDS) if result is not None else DiscoverySnapshot("failed", expires_at=time.time() + FAILURE_BACKOFF_SECONDS)
        if result is not None:
            self._write(snapshot)
        with self._lock:
            self._records[self.path] = snapshot
            if len(self._records) > 512:
                self._records.pop(next(iter(self._records)))
            self._pending.pop(self.path, None)
            pending.set_result(snapshot)

    def ensure(self, loader: Callable[[], dict[str, bool] | None]) -> DiscoverySnapshot:
        """Share an in-flight discovery or perform one on the calling thread."""
        pending, owner = self._begin()
        if owner:
            self._run(pending, loader)
        return pending.result()

    def start(self, loader: Callable[[], dict[str, bool] | None]) -> DiscoverySnapshot:
        """Start at most one background discovery for this configuration."""
        pending, owner = self._begin()
        if owner:
            threading.Thread(target=self._run, args=(pending, loader), daemon=True, name="model-capability-probe").start()
        return self.snapshot()


_PROBE_NOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "Do nothing. Call this tool when the user asks you to.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def probe_capabilities(provider, *, model: str, api_key: str, base_url: str, timeout: int) -> dict[str, bool] | None:
    """Discover tool and JSON support against an immutable model target."""
    if provider is None:
        return None
    deadline = time.monotonic() + min(timeout, PROBE_BUDGET_SECONDS)
    try:
        tool_result = provider.stream_chat(
            messages=[{"role": "user", "content": "Call the noop tool."}],
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
            tools=[_PROBE_NOOP_TOOL],
            max_tokens=64,
            timeout=timeout,
            deadline=deadline,
        )
    except Exception as exc:
        logger.debug("tool-calling probe failed for %s: %s", model, exc)
        return None
    tool_calling = bool(tool_result.tool_calls)
    parallel_tool_calls = False
    if tool_calling:
        try:
            parallel_result = provider.stream_chat(
                messages=[{"role": "user", "content": "Call the noop tool twice — make both calls in this single response."}],
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0,
                tools=[_PROBE_NOOP_TOOL],
                max_tokens=128,
                timeout=timeout,
                deadline=deadline,
            )
            parallel_tool_calls = len(parallel_result.tool_calls) >= 2
        except Exception as exc:
            logger.debug("parallel-tool probe failed for %s: %s", model, exc)
            return None
    json_mode = False
    if not tool_calling:
        try:
            json_result = provider.stream_chat(
                messages=[{"role": "user", "content": 'Reply with exactly: {"ok": true}'}],
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=64,
                timeout=timeout,
                deadline=deadline,
            )
            json.loads(json_result.text)
            json_mode = True
        except Exception as exc:
            logger.debug("json-mode probe failed for %s: %s", model, exc)
            return None
    return {"tool_calling": tool_calling, "json_mode": json_mode,
            "parallel_tool_calls": parallel_tool_calls}


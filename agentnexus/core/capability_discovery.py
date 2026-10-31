"""Cached capability discovery shared by clients using the same model configuration."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from agentnexus.core.config import get_config_dir

logger = logging.getLogger(__name__)

FAILURE_BACKOFF_SECONDS = 30
PROBE_BUDGET_SECONDS = 5.0
_FEATURES = frozenset({"tool_calling", "json_mode", "json_schema", "parallel_tool_calls", "thinking", "stream_options"})


@dataclass(frozen=True)
class ProbeEvidence:
    """Independent confirmed capabilities and any unfinished probe attempts."""

    values: dict[str, bool] = field(default_factory=dict)
    transient_failure: bool = False


def unsupported_capability(error: Exception) -> str | None:
    """Classify explicit protocol rejection without confusing transport or data errors."""
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return None
    body = getattr(error, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    message = str(detail.get("message") or str(error)).lower()
    status = getattr(error, "status_code", None)
    if status is None:
        match = re.search(r"(?:error code|status(?: code)?|http)\s*[:=]?\s*(\d{3})|^\s*(\d{3})\b", message)
        if match:
            status = int(match.group(1) or match.group(2))
    if status is not None and status not in {400, 422}:
        return None
    error_type = type(error).__name__.lower()
    if any(term in error_type for term in ("timeout", "connection", "ratelimit")):
        return None
    if status is None and any(term in message for term in (
        "timed out", "timeout", "connection reset", "connection refused", "network error", "rate limit",
    )):
        return None
    code = str(detail.get("code") or "").lower()
    rejected = any(token in message for token in (
        "not support", "unsupported", "doesn't support", "unknown parameter", "unrecognized parameter",
        "not allowed", "not available",
    )) or code in {"unsupported_parameter", "unknown_parameter", "unsupported_feature"}
    if not rejected:
        return None
    param = str(detail.get("param") or "").lower()
    if param == "reasoning_effort" and code == "unsupported_value":
        return None
    if param.startswith(("tools[", "tools.", "functions[", "functions.")):
        return None
    if any(term in message for term in ("tool arguments", "function arguments", "invalid schema", "invalid tool schema")):
        return None
    target = param + " " + message
    if "parallel_tool_calls" in target:
        return "parallel_tool_calls"
    if "stream_options" in target:
        return "stream_options"
    if "reasoning_effort" in target or re.search(r"\bthinking\b", target):
        return "thinking"
    if "json_schema" in target:
        return "json_schema"
    if "response_format" in target or "json_object" in target or "json mode" in target:
        return "json_mode"
    if param in {"tools", "tool_choice", "functions", "function_call"} or re.search(
        r"\btools\b|\btool[_ ]calling\b|\bfunction[_ ]call(?:ing)?\b", message,
    ):
        return "tool_calling"
    return None


@dataclass(frozen=True)
class DiscoverySnapshot:
    """The last completed discovery or its current progress."""

    status: Literal["unknown", "probing", "ready", "failed"] = "unknown"
    result: dict[str, bool] | None = None
    retry_after: float = 0


class CapabilityDiscovery:
    """Reuse confirmed facts and transient backoff for one authenticated endpoint."""

    _lock = threading.RLock()
    _records: dict[Path, DiscoverySnapshot] = {}
    _pending: dict[Path, Future[DiscoverySnapshot]] = {}

    def __init__(self, model: str, base_url: str, api_key: str, configuration: dict):
        parts = urlsplit(base_url.strip().rstrip("/"))
        scheme, authority = parts.scheme.lower(), parts.netloc.lower()
        default_port = {"https": ":443", "http": ":80"}.get(scheme)
        if default_port and authority.endswith(default_port):
            authority = authority[:-len(default_port)]
        normalized_url = urlunsplit((scheme, authority, parts.path, parts.query, ""))
        credential = hashlib.sha256(api_key.encode()).hexdigest()
        identity = json.dumps([model.strip(), normalized_url, credential], sort_keys=True)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        self.path = get_config_dir() / "capability-cache" / f"{digest}.json"
        legacy_config = {key: value for key, value in configuration.items() if key != "unknown_features"}
        legacy_identity = json.dumps([model, base_url.rstrip("/"), api_key, legacy_config], sort_keys=True)
        self._legacy_path = self.path.with_name(hashlib.sha256(legacy_identity.encode()).hexdigest() + ".json")

    def snapshot(self) -> DiscoverySnapshot:
        """Read a local cache entry without starting or waiting for a probe."""
        with self._lock:
            snapshot = self._current()
            if self.path in self._pending:
                return DiscoverySnapshot("probing", snapshot.result, snapshot.retry_after)
            return snapshot

    def _current(self) -> DiscoverySnapshot:
        snapshot = self._read()
        if snapshot is None:
            snapshot = self._records.get(self.path, DiscoverySnapshot())
        values = dict(snapshot.result) if snapshot.result else None
        retry_after = snapshot.retry_after if snapshot.retry_after > time.time() else 0
        status = "failed" if retry_after else "ready" if values else "unknown"
        return DiscoverySnapshot(status, values, retry_after)

    def _read(self) -> DiscoverySnapshot | None:
        try:
            path = self.path if self.path.exists() else self._legacy_path
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] not in {1, 2}:
                return None
            result = data["result"]
            if not isinstance(result, dict) or not result.keys() <= _FEATURES or any(type(value) is not bool for value in result.values()):
                return None
            if data["version"] == 1:
                expiry = data["expires_at"]
                if type(expiry) not in {int, float} or not math.isfinite(expiry):
                    return None
                if result.keys() != {"tool_calling", "json_mode", "parallel_tool_calls"}:
                    return None
                confirmed = {feature: True for feature, supported in result.items() if supported}
                snapshot = DiscoverySnapshot("ready" if confirmed else "unknown", confirmed or None)
                self._write(snapshot)
                return snapshot
            retry_after = data["retry_after"]
            if type(retry_after) not in {int, float} or not math.isfinite(retry_after) or retry_after < 0:
                return None
            return DiscoverySnapshot(result=result or None, retry_after=retry_after)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _write(self, snapshot: DiscoverySnapshot) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump({"version": 2, "retry_after": snapshot.retry_after, "result": snapshot.result or {}}, output)
            os.replace(temporary, self.path)
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def record(self, feature: str, supported: bool) -> None:
        """Persist one confirmed outcome without discarding other capability facts."""
        if feature not in _FEATURES or type(supported) is not bool:
            raise ValueError("Capability evidence requires a known feature and a Boolean value")
        with self._lock:
            snapshot = self._current()
            values = dict(snapshot.result or {})
            if values.get(feature) is supported:
                return
            values[feature] = supported
            updated = DiscoverySnapshot("ready", values, snapshot.retry_after)
            self._records[self.path] = updated
            self._write(updated)

    def _begin(self, force: bool) -> tuple[Future[DiscoverySnapshot], bool]:
        with self._lock:
            if self.path in self._pending:
                return self._pending[self.path], False
            snapshot = self._current()
            if not force and (snapshot.result is not None or snapshot.retry_after):
                done = Future()
                done.set_result(snapshot)
                return done, False
            pending = Future()
            self._pending[self.path] = pending
            return pending, True

    def _run(self, pending: Future[DiscoverySnapshot], loader: Callable[[], ProbeEvidence]) -> None:
        try:
            try:
                evidence = loader()
            except Exception:
                evidence = ProbeEvidence(transient_failure=True)
            with self._lock:
                current = self._current()
                values = {**(current.result or {}), **evidence.values}
                failed = evidence.transient_failure or not values
                retry_after = time.time() + FAILURE_BACKOFF_SECONDS if failed else 0
                status = "failed" if retry_after else "ready"
                snapshot = DiscoverySnapshot(status, values or None, retry_after)
                self._write(snapshot)
                self._records[self.path] = snapshot
                if len(self._records) > 512:
                    self._records.pop(next(iter(self._records)))
                self._pending.pop(self.path, None)
                pending.set_result(snapshot)
        except BaseException as exc:
            with self._lock:
                self._pending.pop(self.path, None)
                pending.set_exception(exc)

    def ensure(self, loader: Callable[[], ProbeEvidence], *, force: bool = False) -> DiscoverySnapshot:
        """Share an in-flight discovery or perform one on the calling thread."""
        pending, owner = self._begin(force)
        if owner:
            self._run(pending, loader)
        return pending.result()

    def start(self, loader: Callable[[], ProbeEvidence], *, force: bool = False) -> DiscoverySnapshot:
        """Start at most one background discovery for this configuration."""
        pending, owner = self._begin(force)
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


def probe_capabilities(provider, *, model: str, api_key: str, base_url: str, timeout: int,
                       on_evidence: Callable[[str, bool], None] | None = None) -> ProbeEvidence:
    """Confirm independent protocols under one total request budget."""
    if provider is None:
        return ProbeEvidence(transient_failure=True)
    values: dict[str, bool] = {}

    def confirm(feature: str, supported: bool) -> None:
        values[feature] = supported
        if on_evidence is not None:
            on_evidence(feature, supported)

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
        rejected = unsupported_capability(exc)
        if rejected is None:
            return ProbeEvidence(values, transient_failure=True)
        confirm(rejected, False)
    else:
        if tool_result.tool_calls:
            confirm("tool_calling", True)
    if values.get("tool_calling") is True:
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
            if len(parallel_result.tool_calls) >= 2:
                confirm("parallel_tool_calls", True)
        except Exception as exc:
            logger.debug("parallel-tool probe failed for %s: %s", model, exc)
            rejected = unsupported_capability(exc)
            if rejected == "parallel_tool_calls":
                confirm(rejected, False)
                return ProbeEvidence(values)
            return ProbeEvidence(values, transient_failure=True)
        return ProbeEvidence(values)
    if values.get("tool_calling") is False:
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
        except Exception as exc:
            logger.debug("json-mode probe failed for %s: %s", model, exc)
            rejected = unsupported_capability(exc)
            if rejected == "json_mode":
                confirm(rejected, False)
            else:
                return ProbeEvidence(values, transient_failure=True)
        else:
            try:
                json.loads(json_result.text)
            except (ValueError, TypeError):
                return ProbeEvidence(values)
            confirm("json_mode", True)
    return ProbeEvidence(values)


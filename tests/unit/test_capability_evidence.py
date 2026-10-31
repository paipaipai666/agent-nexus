"""Capability evidence survives independent probe failures and process restarts."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agentnexus.core.capability_discovery import probe_capabilities
from agentnexus.core.providers.base import StreamResult


class EndpointError(Exception):
    def __init__(self, message, *, status_code=400, param=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"message": message, "param": param, "code": code}}


class Provider:
    def __init__(self, *outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []

    def stream_chat(self, **kwargs):
        self.requests.append(kwargs)
        result = next(self.outcomes)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("error", [TimeoutError("deadline"), EndpointError("Rate limit", status_code=429)])
def test_confirmed_native_tools_survive_optional_probe_failure(error):
    provider = Provider(StreamResult(tool_calls=[{"name": "noop", "arguments": {}}]), error)
    evidence = probe_capabilities(provider, model="fixture", api_key="fixture-key",
                                  base_url="https://example.invalid", timeout=5)
    assert evidence is not None
    assert evidence.values == {"tool_calling": True}
    assert evidence.transient_failure is True
    assert len(provider.requests) == 2


@pytest.mark.parametrize("param, message, expected", [
    ("tools", "This model does not support tools", "tool_calling"),
    (None, "Function calling is not supported", "tool_calling"),
    ("parallel_tool_calls", "Unsupported parameter alongside tools", "parallel_tool_calls"),
    ("response_format", "This model does not support json_object", "json_mode"),
    ("response_format.type", "json_schema is not supported", "json_schema"),
    ("reasoning_effort", "Unsupported parameter", "thinking"),
    ("stream_options", "Unknown parameter in tools request", "stream_options"),
])
def test_only_the_rejected_protocol_parameter_is_classified(param, message, expected):
    from agentnexus.core.capability_discovery import unsupported_capability

    assert unsupported_capability(EndpointError(message, param=param)) == expected


@pytest.mark.parametrize("error", [
    EndpointError("tools unsupported", status_code=429),
    EndpointError("tools unsupported", status_code=401),
    EndpointError("tools unsupported", status_code=403),
    EndpointError("tools unsupported", status_code=500),
    TimeoutError("tools unsupported during a timeout"),
    ConnectionError("tools unsupported while connecting"),
    EndpointError("Invalid tool arguments: unsupported property 'foo'", param="tools"),
    EndpointError("Unsupported property", param="tools[0].function.parameters"),
    EndpointError("Invalid tools schema", param="tools"),
    EndpointError("Invalid value", param="reasoning_effort", code="unsupported_value"),
    RuntimeError("Error code: 429 tools unsupported"),
    RuntimeError("connection reset while sending tools: unsupported transport"),
    RuntimeError("request timed out with tools unsupported by the gateway"),
    type("APITimeoutError", (Exception,), {})("tools unsupported while waiting for a response"),
])
def test_transport_and_business_parameter_errors_do_not_disable_capabilities(error):
    from agentnexus.core.capability_discovery import unsupported_capability

    assert unsupported_capability(error) is None


def test_known_capabilities_reuse_a_stable_identity_across_processes(temp_agentnexus_home):
    from agentnexus.core.capability_discovery import CapabilityDiscovery

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1/", "private-fixture-key",
                                    {"supports_thinking": True, "max_output_tokens": 32})
    discovery.record("tool_calling", True)
    program = '''
import json
from agentnexus.core.capability_discovery import CapabilityDiscovery
def forbidden():
    raise AssertionError("Known capability was probed again")
d = CapabilityDiscovery("fixture", "https://example.invalid/v1", "private-fixture-key",
                        {"supports_thinking": False, "max_output_tokens": 4096})
print(json.dumps(d.ensure(forbidden).result, sort_keys=True))
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", program],
                            cwd=os.getcwd(), text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tool_calling": True}
    assert "private-fixture-key" not in discovery.path.read_text(encoding="utf-8")


def test_confirmed_evidence_does_not_expire_after_one_day(monkeypatch):
    from agentnexus.core.capability_discovery import CapabilityDiscovery, ProbeEvidence

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})
    original = time.time()
    assert discovery.ensure(lambda: ProbeEvidence({"tool_calling": True})).result == {"tool_calling": True}
    monkeypatch.setattr("agentnexus.core.capability_discovery.time.time", lambda: original + 172800)
    assert discovery.ensure(lambda: pytest.fail("Confirmed evidence expired")).result == {"tool_calling": True}


def test_expired_legacy_success_is_reused_and_upgraded(temp_agentnexus_home):
    from agentnexus.core.capability_discovery import CapabilityDiscovery

    configuration = {"supports_thinking": True, "max_output_tokens": 64}
    identity = json.dumps(["fixture", "https://example.invalid/v1", "fixture-key", configuration], sort_keys=True)
    directory = temp_agentnexus_home / "capability-cache"
    directory.mkdir()
    legacy = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    legacy.write_text(json.dumps({"version": 1, "expires_at": 0,
                                  "result": {"tool_calling": True, "json_mode": False, "parallel_tool_calls": False}}), encoding="utf-8")
    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", configuration)
    snapshot = discovery.ensure(lambda: pytest.fail("Previously confirmed legacy capability was probed"))
    assert snapshot.result == {"tool_calling": True}
    assert json.loads(discovery.path.read_text(encoding="utf-8"))["version"] == 2


def test_legacy_false_flags_remain_unknown_without_explicit_rejection_evidence(temp_agentnexus_home):
    from agentnexus.core.capability_discovery import CapabilityDiscovery

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})
    directory = temp_agentnexus_home / "capability-cache"
    directory.mkdir()
    discovery._legacy_path.write_text(json.dumps({
        "version": 1, "expires_at": 0,
        "result": {"tool_calling": False, "json_mode": True, "parallel_tool_calls": False},
    }), encoding="utf-8")
    assert discovery.snapshot().result == {"json_mode": True}


def test_json_probe_records_an_explicit_parameter_rejection_from_a_value_error():
    class RejectedValue(ValueError):
        status_code = 400
        body = {"error": {"message": "response_format unsupported", "param": "response_format"}}

    provider = Provider(EndpointError("tools unsupported", param="tools"), RejectedValue("response_format unsupported"))
    evidence = probe_capabilities(provider, model="fixture", api_key="fixture-key",
                                  base_url="https://example.invalid/v1", timeout=5)
    assert evidence.values == {"tool_calling": False, "json_mode": False}


def test_transient_failure_backoff_survives_a_new_process():
    from agentnexus.core.capability_discovery import CapabilityDiscovery, ProbeEvidence

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})
    failed = discovery.ensure(lambda: ProbeEvidence(transient_failure=True))
    assert failed.status == "failed"
    assert failed.result is None
    assert failed.retry_after > time.time()
    program = '''
from agentnexus.core.capability_discovery import CapabilityDiscovery
def forbidden():
    raise AssertionError("Backoff was lost across restart")
print(CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {}).ensure(forbidden).status)
'''
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", program],
                            cwd=os.getcwd(), text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "failed"


def test_forced_refresh_failure_keeps_each_confirmed_fact():
    from agentnexus.core.capability_discovery import CapabilityDiscovery, ProbeEvidence

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})
    discovery.record("tool_calling", True)
    discovery.record("json_mode", False)
    failed = discovery.ensure(lambda: ProbeEvidence(transient_failure=True), force=True)
    assert failed.status == "failed"
    assert failed.result == {"tool_calling": True, "json_mode": False}
    assert failed.retry_after > time.time()
    refreshed = discovery.ensure(lambda: ProbeEvidence({"parallel_tool_calls": True}), force=True)
    assert refreshed.result == {"tool_calling": True, "json_mode": False, "parallel_tool_calls": True}
    assert refreshed.retry_after == 0


def test_concurrent_clients_share_one_probe_and_publish_partial_evidence():
    from agentnexus.core.capability_discovery import CapabilityDiscovery, ProbeEvidence

    first = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})
    second = CapabilityDiscovery("fixture", "https://example.invalid/v1/", "fixture-key", {"max_output_tokens": 500})
    entered, release = threading.Event(), threading.Event()
    calls = []

    def probe():
        calls.append("probe")
        first.record("tool_calling", True)
        entered.set()
        assert release.wait(timeout=5)
        return ProbeEvidence({"tool_calling": True, "parallel_tool_calls": True})

    with ThreadPoolExecutor(max_workers=2) as workers:
        one = workers.submit(first.ensure, probe)
        assert entered.wait(timeout=5)
        try:
            assert second.snapshot().result == {"tool_calling": True}
            assert second.snapshot().status == "probing"
            two = workers.submit(second.ensure, probe)
        finally:
            release.set()
        assert one.result(timeout=5).result == two.result(timeout=5).result == {"tool_calling": True, "parallel_tool_calls": True}
    assert calls == ["probe"]


def test_cache_identity_normalizes_host_and_default_port_without_sharing_credentials():
    from agentnexus.core.capability_discovery import CapabilityDiscovery

    discovery = CapabilityDiscovery("fixture", "HTTPS://EXAMPLE.INVALID:443/v1/", "fixture-key", {})
    discovery.record("tool_calling", True)
    same = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key",
                              {"unknown_features": frozenset({"json_mode"})})
    assert same.snapshot().result == {"tool_calling": True}
    assert CapabilityDiscovery("fixture", "https://example.invalid/v1", "different-key", {}).snapshot().result is None
    assert CapabilityDiscovery("other-model", "https://example.invalid/v1", "fixture-key", {}).snapshot().result is None


def test_inconclusive_probe_is_backed_off_without_recording_unsupported():
    from agentnexus.core.capability_discovery import CapabilityDiscovery, ProbeEvidence

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})
    snapshot = discovery.ensure(lambda: ProbeEvidence())
    assert snapshot.status == "failed"
    assert snapshot.result is None
    assert snapshot.retry_after > time.time()
    assert discovery.ensure(lambda: pytest.fail("Inconclusive probe was immediately repeated")).result is None


def test_interrupted_discovery_does_not_leave_a_pending_probe():
    from agentnexus.core.capability_discovery import CapabilityDiscovery, ProbeEvidence

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})

    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        discovery.ensure(interrupted)
    assert discovery.snapshot().status != "probing"
    assert discovery.ensure(lambda: ProbeEvidence({"tool_calling": True})).result == {"tool_calling": True}


@pytest.mark.parametrize("outcome", [StreamResult(text="I can answer directly"), StreamResult(finish_reason="length")])
def test_empty_tool_call_result_does_not_prove_protocol_rejection(outcome):
    provider = Provider(outcome)
    evidence = probe_capabilities(provider, model="fixture", api_key="fixture-key",
                                  base_url="https://example.invalid", timeout=5)
    assert evidence.values == {}
    assert len(provider.requests) == 1


def test_explicit_native_rejection_probes_the_next_protocol_under_the_same_budget():
    provider = Provider(EndpointError("tools are not supported", param="tools"), StreamResult(text='{"ok":true}'))
    evidence = probe_capabilities(provider, model="fixture", api_key="fixture-key",
                                  base_url="https://example.invalid", timeout=5)
    assert evidence.values == {"tool_calling": False, "json_mode": True}
    assert provider.requests[0]["tools"][0]["function"]["name"] == "noop"
    assert provider.requests[1]["response_format"] == {"type": "json_object"}
    assert provider.requests[0]["deadline"] == provider.requests[1]["deadline"]


def test_probe_publishes_native_evidence_before_optional_probe_returns():
    from agentnexus.core.capability_discovery import CapabilityDiscovery

    discovery = CapabilityDiscovery("fixture", "https://example.invalid/v1", "fixture-key", {})

    class ObservedProvider:
        calls = 0

        def stream_chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return StreamResult(tool_calls=[{"name": "noop", "arguments": {}}])
            assert json.loads(discovery.path.read_text(encoding="utf-8"))["result"] == {"tool_calling": True}
            raise EndpointError("Rate limit", status_code=429)

    final = discovery.ensure(lambda: probe_capabilities(ObservedProvider(), model="fixture", api_key="fixture-key",
                                                        base_url="https://example.invalid/v1", timeout=5,
                                                        on_evidence=discovery.record))
    assert final.result == {"tool_calling": True}
    assert final.status == "failed"


def test_rejecting_an_effort_value_does_not_prove_thinking_is_unsupported():
    from agentnexus.core.capability_discovery import unsupported_capability

    error = EndpointError("Unsupported value: reasoning_effort does not support high", param="reasoning_effort",
                          code="unsupported_value")
    assert unsupported_capability(error) is None

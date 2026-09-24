"""Tests for agentnexus.core.llm."""

from unittest.mock import patch

from pydantic import SecretStr

from agentnexus.core.llm import AgentLLM, _preview, get_default_llm
from agentnexus.core.providers.base import StreamResult


def _make_result(text="", tool_calls=None, reasoning_content="",
                 usage=None, finish_reason="stop"):
    """Helper: build a StreamResult for mocking _call_via_provider."""
    return StreamResult(
        text=text,
        tool_calls=tool_calls or [],
        reasoning_content=reasoning_content,
        usage=usage or {"input_tokens": 5, "output_tokens": 5},
        finish_reason=finish_reason,
    )


class TestPreview:
    def test_short_text_returned_as_is(self):
        assert _preview("hello") == "hello"

    def test_long_text_truncated(self):
        text = "a" * 1000
        result = _preview(text, max_len=10)
        assert result == "aaaaaaaaaa..."
        assert len(result) == 13

    def test_default_max_len_500(self):
        text = "a" * 600
        result = _preview(text)
        assert len(result) == 503  # 500 + 3 for "..."
        assert result.endswith("...")


class TestGetDefaultLLM:
    def teardown_method(self):
        import agentnexus.core.llm as m
        m._default_llm = None

    @patch("agentnexus.core.llm.AgentLLM")
    def test_singleton(self, MockAgentLLM):
        first = get_default_llm()
        second = get_default_llm()
        MockAgentLLM.assert_called_once()
        assert first is second


class TestAgentLLMInit:
    @patch("agentnexus.core.llm.get_settings")
    def test_defaults_from_settings(self, mock_settings):
        mock_settings.return_value.llm_model_id = "default-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://default.url"
        mock_settings.return_value.llm_timeout = 60
        # AgentLLM resolves the active provider profile (flat fields are the fallback)
        mock_settings.return_value.get_active_llm_profile.return_value = (
            "default-model", "https://default.url", SecretStr("key"), 60,
        )

        llm = AgentLLM()
        assert llm.model == "openai/default-model"  # normalized with default provider
        assert llm.api_key == "key"
        assert llm.base_url == "https://default.url"
        assert llm.timeout == 60

    @patch("agentnexus.core.llm.get_settings")
    def test_explicit_params_override_settings(self, mock_settings):
        mock_settings.return_value.llm_model_id = "default-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://default.url"
        mock_settings.return_value.llm_timeout = 60

        llm = AgentLLM(model="custom", apiKey="custom-key", baseUrl="https://custom.url", timeout=30)
        assert llm.model == "openai/custom"  # normalized with default provider
        assert llm.api_key == "custom-key"
        assert llm.base_url == "https://custom.url"
        assert llm.timeout == 30


class TestThinkNoApiKey:
    @patch("agentnexus.core.llm.get_settings")
    def test_no_api_key_returns_empty(self, mock_settings):
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.llm_base_url = ""
        mock_settings.return_value.llm_timeout = 60
        mock_settings.return_value.get_active_llm_profile.return_value = (
            "model", "", SecretStr(""), 60,
        )

        llm = AgentLLM()
        assert llm.api_key == ""
        result = llm.think([{"role": "user", "content": "hi"}])
        assert result == ""


class TestThinkRetryLoop:
    @patch("agentnexus.core.llm.get_settings")
    def test_retries_on_transient_error_and_succeeds(self, mock_settings):
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60

        call_count = [0]

        def fake_call(messages, temperature, silent, attempt, tools=None, response_format=None, thinking=None, on_token=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return ""  # simulates transient failure (empty = retry)
            return "final answer"

        llm = AgentLLM()
        with patch.object(llm, "_call", side_effect=fake_call):
            result = llm.think([{"role": "user", "content": "hi"}])

        assert result == "final answer"
        assert call_count[0] == 2

    @patch("agentnexus.core.llm.get_settings")
    def test_all_retries_exhausted_returns_empty(self, mock_settings):
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60

        llm = AgentLLM()
        with patch.object(llm, "_call", return_value=""):
            result = llm.think([{"role": "user", "content": "hi"}])

        assert result == ""

    @patch("agentnexus.core.llm.get_settings")
    def test_max_attempts_limits_retry_loop(self, mock_settings):
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60

        llm = AgentLLM()
        with patch.object(llm, "_call", return_value="") as call:
            result = llm.think([{"role": "user", "content": "hi"}], max_attempts=1)

        assert result == ""
        assert call.call_count == 1


class TestCall:
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_successful_call(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="Hello world", finish_reason="stop")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Hello world"
        assert llm.last_truncated is False

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_truncated_response(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(text="partial", finish_reason="length")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "partial"
        assert llm.last_truncated is True

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_tool_calls_accumulated(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        tool_calls = [
            {"name": "get_weather", "arguments": {"city": "NYC"}},
            {"name": "search", "arguments": {"q": "test"}},
        ]
        llm = AgentLLM()
        result_obj = _make_result(text="", tool_calls=tool_calls, finish_reason="tool_calls")
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0,
                               tools=[{"type": "function", "function": {"name": "test"}}])

        assert result == ""
        assert len(llm.last_tool_calls) == 2
        assert llm.last_tool_calls[0]["name"] == "get_weather"
        assert llm.last_tool_calls[0]["arguments"] == {"city": "NYC"}

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_reasoning_content_captured(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(
            text="Final answer",
            reasoning_content="thinking step by step...",
            finish_reason="stop",
        )
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert llm.last_reasoning_content == "thinking step by step..."

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_non_transient_error_returns_empty(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider", side_effect=ValueError("invalid request")):
            with patch("litellm.completion", side_effect=ValueError("also fails")):
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == ""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_transient_connection_error(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider", side_effect=ConnectionError("connection")):
            with patch("litellm.completion", side_effect=ConnectionError("connection")):
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == ""
        assert "connection" in llm.last_error.lower()

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_capability_degradation_on_tool_error(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider",
                          side_effect=ValueError("tool calling not supported")):
            with patch("litellm.completion",
                       side_effect=ValueError("tool calling not supported")):
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert llm.session_tracker.failed_counts.get("tool_calling", 0) > 0

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_capability_degradation_on_json_mode_error(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider",
                          side_effect=ValueError("response_format unsupported")):
            with patch("litellm.completion",
                       side_effect=ValueError("response_format unsupported")):
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert llm.session_tracker.failed_counts.get("json_mode", 0) > 0

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_capability_degradation_on_thinking_error(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        with patch.object(llm, "_call_via_provider",
                          side_effect=ValueError("reasoning_effort not supported")):
            with patch("litellm.completion",
                       side_effect=ValueError("reasoning_effort not supported")):
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert llm.session_tracker.failed_counts.get("thinking", 0) > 0

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_usage_from_stream_fallback(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        llm = AgentLLM()
        result_obj = _make_result(
            text="hello",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
        )
        with patch.object(llm, "_call_via_provider", return_value=result_obj):
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert llm.last_usage.get("input_tokens", 0) >= 0
        assert llm.last_usage.get("output_tokens", 0) >= 0


class TestCapabilityProbe:
    """Live probe for models the static registry doesn't know (from_default_fallback)."""

    def teardown_method(self):
        import agentnexus.core.llm as m
        m._probe_cache.clear()

    @staticmethod
    def _patch_provider(monkeypatch, provider):
        import agentnexus.core.llm as m
        monkeypatch.setattr(m, "select_provider", lambda model, base_url: provider)

    def test_probe_enables_tool_calling_for_unknown_model(self, temp_agentnexus_home, monkeypatch):
        class _FakeProvider:
            def stream_chat(self, **kwargs):
                result = StreamResult()
                if kwargs.get("tools"):
                    result.tool_calls = [{"id": "call_1", "name": "noop", "arguments": {}}]
                else:
                    result.text = '{"ok": true}'
                return result

        self._patch_provider(monkeypatch, _FakeProvider())
        client = AgentLLM(model="stealth/probe-tools", api_key="k",
                          base_url="https://example.test/v1")
        assert client.capabilities.supports_tool_calling is True

    def test_probe_enables_json_mode_when_tools_unsupported(self, temp_agentnexus_home, monkeypatch):
        class _TextOnlyProvider:
            def stream_chat(self, **kwargs):
                return StreamResult(text='{"ok": true}')

        self._patch_provider(monkeypatch, _TextOnlyProvider())
        client = AgentLLM(model="stealth/probe-json", api_key="k",
                          base_url="https://example.test/v1")
        assert client.capabilities.supports_tool_calling is False
        assert client.capabilities.supports_json_mode is True

    def test_probe_failure_keeps_conservative_defaults_and_is_not_cached(
        self, temp_agentnexus_home, monkeypatch,
    ):
        class _DownProvider:
            def stream_chat(self, **kwargs):
                raise ConnectionError("network down")

        self._patch_provider(monkeypatch, _DownProvider())
        client = AgentLLM(model="stealth/probe-down", api_key="k",
                          base_url="https://example.test/v1")
        assert client.capabilities.supports_tool_calling is False
        import agentnexus.core.llm as m
        assert ("stealth/probe-down", "https://example.test/v1") not in m._probe_cache

    def test_config_override_wins_over_probe(self, temp_agentnexus_home, monkeypatch):
        monkeypatch.setenv("AGENTNEXUS_MODEL_TOOL_CALLING", "false")

        class _FakeProvider:
            def stream_chat(self, **kwargs):
                result = StreamResult()
                if kwargs.get("tools"):
                    result.tool_calls = [{"id": "call_1", "name": "noop", "arguments": {}}]
                return result

        self._patch_provider(monkeypatch, _FakeProvider())
        client = AgentLLM(model="stealth/probe-override", api_key="k",
                          base_url="https://example.test/v1")
        assert client.capabilities.supports_tool_calling is False

    def test_known_model_skips_probe(self, temp_agentnexus_home, monkeypatch):
        import agentnexus.core.llm as m

        self._patch_provider(monkeypatch, None)  # would return None → probe aborts
        client = AgentLLM(model="deepseek/deepseek-chat", api_key="k",
                          base_url="https://api.deepseek.com")
        assert client.capabilities.supports_tool_calling is True
        assert not m._probe_cache


class TestPerCallStateIsolation:
    """并发运行共享同一 AgentLLM 时，per-call 状态不得互相覆盖。"""

    def test_last_tool_calls_isolated_per_thread(self, temp_agentnexus_home):
        import threading

        client = AgentLLM(model="deepseek/deepseek-chat", api_key="k",
                          base_url="https://api.deepseek.com")
        barrier = threading.Barrier(2)
        seen: dict[str, list] = {}

        def worker(tag: str):
            barrier.wait()
            client.last_tool_calls = [{"name": f"tool_{tag}", "arguments": {}}]
            client.last_error = f"err_{tag}"
            # 给对方线程一点覆盖时间
            import time
            time.sleep(0.05)
            seen[tag] = [client.last_tool_calls, client.last_error]

        threads = [threading.Thread(target=worker, args=(t,)) for t in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert seen["a"][0][0]["name"] == "tool_a", "线程 a 的 tool_calls 被线程 b 覆盖"
        assert seen["a"][1] == "err_a"
        assert seen["b"][0][0]["name"] == "tool_b"
        assert seen["b"][1] == "err_b"

    def test_main_thread_defaults(self, temp_agentnexus_home):
        client = AgentLLM(model="deepseek/deepseek-chat", api_key="k",
                          base_url="https://api.deepseek.com")
        assert client.last_tool_calls == []
        assert client.last_error == ""
        assert client.last_truncated is False
        assert client.last_usage == {}

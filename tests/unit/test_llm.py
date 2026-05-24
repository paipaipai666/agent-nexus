"""Tests for agentnexus.core.llm."""

from unittest.mock import MagicMock, patch

from agentnexus.core.llm import AgentLLM, _preview, get_default_llm


class MockChunk:
    """Simulate a litellm streaming chunk."""

    def __init__(self, content="", finish_reason="", tool_calls=None,
                 reasoning_content=None, usage=None):
        delta = MagicMock()
        delta.content = content
        delta.tool_calls = tool_calls or []
        delta.reasoning_content = reasoning_content
        self.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
        self.usage = usage


def _chunk_iter(*chunks):
    """Helper: yield mock chunks in sequence."""
    yield from chunks


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

        llm = AgentLLM()
        assert llm.model == "default-model"
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
        assert llm.model == "custom"
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

        llm = AgentLLM()
        assert llm.api_key == ""
        result = llm.think([{"role": "user", "content": "hi"}])
        assert result == ""


class TestThinkRetryLoop:
    @patch("agentnexus.core.llm.trace_manager")
    @patch("agentnexus.core.llm.get_settings")
    def test_retries_on_transient_error_and_succeeds(self, mock_settings, mock_trace):
        mock_settings.return_value.llm_model_id = "model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        call_count = [0]

        def fake_completion(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("connection reset by peer")
            return _chunk_iter(MockChunk(content="final answer", finish_reason="stop"))

        with patch("litellm.completion", side_effect=fake_completion):
            with patch("litellm.token_counter", return_value=5):
                llm = AgentLLM()
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


class TestCall:
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_successful_call(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        chunk1 = MockChunk(content="Hello")
        chunk2 = MockChunk(content=" world", finish_reason="stop")

        with patch("litellm.completion", return_value=_chunk_iter(chunk1, chunk2)):
            llm = AgentLLM()
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

        chunk = MockChunk(content="partial", finish_reason="length")

        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            llm = AgentLLM()
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "partial"
        assert llm.last_truncated is True

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_model_prefix_inference(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "deepseek-chat"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        seen_models = []

        def fake_completion(**kwargs):
            seen_models.append(kwargs["model"])
            return _chunk_iter(MockChunk(content="ok", finish_reason="stop"))

        with patch("litellm.completion", side_effect=fake_completion):
            llm = AgentLLM()
            llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert seen_models == ["deepseek/deepseek-chat"]

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_tool_calls_accumulated(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        tc1 = {"index": 0, "id": "call_1", "function": {"name": "get_", "arguments": ""}}
        tc2 = {"index": 0, "function": {"name": "weather", "arguments": '{"city": "NYC"}'}}
        tc3 = {"index": 1, "id": "call_2", "function": {"name": "search", "arguments": '{"q": "test"}'}}

        chunks = [
            MockChunk(tool_calls=[tc1], finish_reason="tool_calls"),
            MockChunk(tool_calls=[tc2]),
            MockChunk(tool_calls=[tc3], finish_reason="tool_calls"),
        ]

        with patch("litellm.completion", return_value=_chunk_iter(*chunks)):
            llm = AgentLLM()
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

        chunks = [
            MockChunk(content="", reasoning_content="thinking step by step..."),
            MockChunk(content="Final answer", finish_reason="stop"),
        ]

        with patch("litellm.completion", return_value=_chunk_iter(*chunks)):
            llm = AgentLLM()
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

        with patch("litellm.completion", side_effect=ValueError("invalid request")):
            llm = AgentLLM()
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

        with patch("litellm.completion", side_effect=ConnectionError("connection")):
            llm = AgentLLM()
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result is None
        assert "connection" in llm.last_error.lower()

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_capability_degradation_on_tool_error(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        with patch("litellm.completion",
                   side_effect=ValueError("tool calling not supported")):
            llm = AgentLLM()
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

        with patch("litellm.completion",
                   side_effect=ValueError("response_format unsupported")):
            llm = AgentLLM()
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

        with patch("litellm.completion",
                   side_effect=ValueError("reasoning_effort not supported")):
            llm = AgentLLM()
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

        chunk = MockChunk(content="hello", finish_reason="stop")

        with patch("litellm.completion", return_value=_chunk_iter(chunk)):
            with patch("litellm.token_counter", return_value=5):
                llm = AgentLLM()
                llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert llm.last_usage.get("input_tokens", 0) >= 0
        assert llm.last_usage.get("output_tokens", 0) >= 0

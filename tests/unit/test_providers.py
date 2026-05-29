"""Tests for agentnexus.core.providers — router, OpenAI provider, fallback."""

from unittest.mock import MagicMock, patch

from agentnexus.core.providers.base import StreamResult
from agentnexus.core.providers.router import select_provider


class TestRouter:
    def test_anthropic_model_returns_none(self):
        provider = select_provider("anthropic/claude-4.5", "https://api.anthropic.com")
        assert provider is None

    def test_azure_url_returns_none(self):
        provider = select_provider("openai/gpt-4", "https://myresource.openai.azure.com")
        assert provider is None

    def test_deepseek_returns_openai_provider(self):
        from agentnexus.core.providers.openai_provider import OpenAIProvider
        provider = select_provider("deepseek/deepseek-v4-flash", "https://api.deepseek.com")
        assert isinstance(provider, OpenAIProvider)

    def test_openai_returns_openai_provider(self):
        from agentnexus.core.providers.openai_provider import OpenAIProvider
        provider = select_provider("openai/gpt-4", "https://api.openai.com")
        assert isinstance(provider, OpenAIProvider)

    def test_zhipu_returns_openai_provider(self):
        from agentnexus.core.providers.openai_provider import OpenAIProvider
        provider = select_provider("zhipu/glm-4-flash", "https://open.bigmodel.cn/api/paas/v4/")
        assert isinstance(provider, OpenAIProvider)

    def test_unknown_provider_returns_openai_provider(self):
        from agentnexus.core.providers.openai_provider import OpenAIProvider
        provider = select_provider("custom/model", "https://my-proxy.example.com")
        assert isinstance(provider, OpenAIProvider)

    def test_provider_is_cached(self):
        p1 = select_provider("openai/gpt-4", "https://api.openai.com")
        p2 = select_provider("deepseek/v4", "https://api.deepseek.com")
        assert p1 is p2


class MockOpenAIChunk:
    """Simulate an openai SDK streaming chunk."""

    def __init__(self, content=None, finish_reason=None, tool_calls=None,
                 reasoning_content=None, usage=None):
        delta = MagicMock()
        delta.content = content
        delta.tool_calls = tool_calls or []
        delta.reasoning_content = reasoning_content
        self.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
        self.usage = usage


def _make_tool_delta(index, tc_id, name, arguments):
    """Build a dict-like tool_call delta for testing."""
    return {
        "index": index,
        "id": tc_id,
        "function": {"name": name, "arguments": arguments},
    }


class TestOpenAIProvider:
    def _make_provider(self):
        from agentnexus.core.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()

    def test_simple_text_response(self):
        provider = self._make_provider()
        chunk1 = MockOpenAIChunk(content="Hello")
        chunk2 = MockOpenAIChunk(content=" world", finish_reason="stop")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk1, chunk2]

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            result = provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                api_key="test-key",
                base_url="https://api.openai.com",
            )

        assert result.text == "Hello world"
        assert result.finish_reason == "stop"
        assert result.truncated is False
        assert result.tool_calls == []

    def test_truncated_response(self):
        provider = self._make_provider()
        chunk = MockOpenAIChunk(content="partial", finish_reason="length")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk]

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            result = provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                api_key="test-key",
                base_url="https://api.openai.com",
            )

        assert result.truncated is True

    def test_reasoning_content(self):
        provider = self._make_provider()
        chunk = MockOpenAIChunk(
            content="answer",
            reasoning_content="let me think...",
            finish_reason="stop",
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk]

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            result = provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek/deepseek-v4-flash",
                api_key="test-key",
                base_url="https://api.deepseek.com",
            )

        assert result.reasoning_content == "let me think..."

    def test_tool_calls_accumulated(self):
        provider = self._make_provider()
        tc1 = _make_tool_delta(0, "call_123", "get_weather", '{"loc')
        tc2 = _make_tool_delta(0, None, None, 'ation":"Paris"}')

        chunk1 = MockOpenAIChunk(tool_calls=[tc1])
        chunk2 = MockOpenAIChunk(tool_calls=[tc2], finish_reason="stop")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk1, chunk2]

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            result = provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                api_key="test-key",
                base_url="https://api.openai.com",
                tools=[{"type": "function", "function": {"name": "get_weather"}}],
            )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "call_123"
        assert result.tool_calls[0]["name"] == "get_weather"
        assert result.tool_calls[0]["arguments"] == {"location": "Paris"}

    def test_usage_from_streaming_chunk(self):
        provider = self._make_provider()
        usage_mock = MagicMock()
        usage_mock.prompt_tokens = 10
        usage_mock.completion_tokens = 5
        usage_mock.total_tokens = 15

        chunk = MockOpenAIChunk(
            content="hi", finish_reason="stop", usage=usage_mock,
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk]

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            result = provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                api_key="test-key",
                base_url="https://api.openai.com",
            )

        assert result.usage == {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }

    def test_tools_passed_to_client(self):
        provider = self._make_provider()
        chunk = MockOpenAIChunk(content="ok", finish_reason="stop")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk]

        tools = [{"type": "function", "function": {"name": "test_fn"}}]
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                api_key="test-key",
                base_url="https://api.openai.com",
                tools=tools,
                parallel_tool_calls=True,
            )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["tools"] == tools
        assert call_kwargs["tool_choice"] == "auto"
        assert call_kwargs["parallel_tool_calls"] is True

    def test_stream_options_passed_for_openai(self):
        provider = self._make_provider()
        chunk = MockOpenAIChunk(content="ok", finish_reason="stop")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk]

        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=mock_client):
            provider.stream_chat(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4",
                api_key="test-key",
                base_url="https://api.openai.com",
                stream_options={"include_usage": True},
            )

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["stream_options"] == {"include_usage": True}


class TestStreamResult:
    def test_truncated_on_length(self):
        r = StreamResult(finish_reason="length")
        assert r.truncated is True

    def test_truncated_on_max_tokens(self):
        r = StreamResult(finish_reason="max_tokens")
        assert r.truncated is True

    def test_not_truncated_on_stop(self):
        r = StreamResult(finish_reason="stop")
        assert r.truncated is False

    def test_default_values(self):
        r = StreamResult()
        assert r.text == ""
        assert r.tool_calls == []
        assert r.reasoning_content == ""
        assert r.usage == {}
        assert r.finish_reason == ""


class TestFallbackBehavior:
    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_provider_failure_falls_back_to_litellm(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        from agentnexus.core.llm import AgentLLM

        llm = AgentLLM()

        mock_provider = MagicMock()
        mock_provider.stream_chat.side_effect = ConnectionError("connection refused")

        delta = MagicMock(
            content="fallback", tool_calls=[], reasoning_content=None,
        )
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        with patch("agentnexus.core.llm.select_provider", return_value=mock_provider):
            with patch("litellm.completion", return_value=iter([chunk])):
                result = llm._call(
                    [{"role": "user", "content": "hi"}], 0, True, 0,
                )

        assert result == "fallback"

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_anthropic_skips_provider_goes_to_litellm(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "anthropic/claude-4.5"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://api.anthropic.com"
        mock_settings.return_value.llm_timeout = 60
        mock_trace.active = None

        from agentnexus.core.llm import AgentLLM

        llm = AgentLLM()

        delta = MagicMock(
            content="claude reply", tool_calls=[], reasoning_content=None,
        )
        chunk = MagicMock()
        chunk.choices = [MagicMock(delta=delta, finish_reason="stop")]
        chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        with patch("litellm.completion", return_value=iter([chunk])) as mock_litellm:
            result = llm._call(
                [{"role": "user", "content": "hi"}], 0, True, 0,
            )

        assert result == "claude reply"
        mock_litellm.assert_called_once()

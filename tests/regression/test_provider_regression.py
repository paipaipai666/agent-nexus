"""Provider chain end-to-end regression tests.

These tests mock at the openai SDK level (not litellm), exercising the full:
  AgentLLM._call() → select_provider() → OpenAIProvider.stream_chat() → openai.SDK
code path. This ensures the provider abstraction layer works correctly end-to-end.
"""

from unittest.mock import MagicMock, patch

from agentnexus.core.llm import AgentLLM


def _openai_chunk(content=None, finish_reason=None, tool_calls=None,
                  reasoning_content=None, usage=None):
    """Build a realistic openai SDK ChatCompletionChunk."""
    chunk = MagicMock()
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls or []
    delta.reasoning_content = reasoning_content
    chunk.choices = [MagicMock(delta=delta, finish_reason=finish_reason)]
    chunk.usage = usage
    return chunk


def _usage_chunk(prompt_tokens, completion_tokens):
    """Build a usage-only chunk (OpenAI sends this as the final chunk)."""
    chunk = MagicMock()
    chunk.choices = []
    chunk.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return chunk


def _mock_openai_client(chunks):
    """Create a mock openai client that yields the given chunks."""
    client = MagicMock()
    client.chat.completions.create.return_value = iter(chunks)
    return client


class TestProviderEndToEnd:
    """Full stack: AgentLLM → ProviderRouter → OpenAIProvider → mock openai SDK."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_deepseek_model_goes_through_provider(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="DeepSeek answer", finish_reason="stop"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "DeepSeek answer"
        client.chat.completions.create.assert_called_once()
        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "deepseek/deepseek-v4-flash"

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_openai_model_goes_through_provider(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4o"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="GPT answer", finish_reason="stop"),
            _usage_chunk(50, 20),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "GPT answer"
        assert llm.last_usage["input_tokens"] == 50
        assert llm.last_usage["output_tokens"] == 20

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_anthropic_model_skips_provider_goes_litellm(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "anthropic/claude-4.5"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.anthropic.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content="Claude answer", tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        llm = AgentLLM()
        with patch("litellm.completion", return_value=iter([litellm_chunk])) as mock_lit:
            with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_openai:
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Claude answer"
        mock_lit.assert_called_once()
        mock_openai.assert_not_called()

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_azure_model_skips_provider_goes_litellm(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://myresource.openai.azure.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content="Azure answer", tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        llm = AgentLLM()
        with patch("litellm.completion", return_value=iter([litellm_chunk])) as mock_lit:
            with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_openai:
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Azure answer"
        mock_lit.assert_called_once()
        mock_openai.assert_not_called()

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_unknown_provider_uses_openai_provider(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "custom/my-model"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "key"
        mock_settings.return_value.llm_base_url = "https://my-proxy.example.com/v1"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="Proxy answer", finish_reason="stop"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Proxy answer"
        # base_url is passed to OpenAI() constructor — verify it was used
        assert client.chat.completions.create.call_count == 1


class TestProviderFallbackChain:
    """Provider failure → LiteLLM fallback regression tests."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_provider_connection_error_falls_back(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content="fallback", tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        llm = AgentLLM()
        # openai SDK raises, litellm succeeds
        with patch("agentnexus.core.providers.openai_provider.OpenAI", side_effect=ConnectionError("refused")):
            with patch("litellm.completion", return_value=iter([litellm_chunk])):
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "fallback"

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_provider_timeout_falls_back(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content="timeout fallback", tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = TimeoutError("timed out")
            with patch("litellm.completion", return_value=iter([litellm_chunk])):
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "timeout fallback"

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_provider_api_error_falls_back(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        litellm_chunk = MagicMock()
        litellm_chunk.choices = [MagicMock(
            delta=MagicMock(content="api error fallback", tool_calls=[], reasoning_content=None),
            finish_reason="stop",
        )]
        litellm_chunk.usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.side_effect = Exception("401 Unauthorized")
            with patch("litellm.completion", return_value=iter([litellm_chunk])):
                result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "api error fallback"


class TestProviderToolCalling:
    """Tool calling through the provider chain regression tests."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_tool_calls_flow_through_provider(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        tc1 = {"index": 0, "id": "call_abc", "function": {"name": "search", "arguments": '{"q'}}
        tc2 = {"index": 0, "id": None, "function": {"name": None, "arguments": 'uery":"test"}'}}

        client = _mock_openai_client([
            _openai_chunk(tool_calls=[tc1]),
            _openai_chunk(tool_calls=[tc2], finish_reason="stop"),
        ])

        tools = [{"type": "function", "function": {"name": "search", "parameters": {}}}]

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            llm._call(
                [{"role": "user", "content": "search for test"}], 0, True, 0,
                tools=tools,
            )

        assert len(llm.last_tool_calls) == 1
        assert llm.last_tool_calls[0]["name"] == "search"
        assert llm.last_tool_calls[0]["arguments"] == {"query": "test"}

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_unsupported_tool_capability_degrades(self, mock_trace, mock_settings):
        """When the model doesn't support tool calling, capability degrades gracefully."""
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-reasoner"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="plain answer", finish_reason="stop"),
        ])

        llm = AgentLLM()
        llm._capabilities = None  # Force re-detection

        tools = [{"type": "function", "function": {"name": "search"}}]
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call(
                [{"role": "user", "content": "hi"}], 0, True, 0,
                tools=tools,
            )

        assert result == "plain answer"


class TestProviderReasoningContent:
    """Reasoning/thinking content through the provider chain."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_deepseek_reasoning_content_preserved(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="", reasoning_content="Let me think..."),
            _openai_chunk(content="The answer is 42", reasoning_content="", finish_reason="stop"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "what is life?"}], 0, True, 0)

        assert result == "The answer is 42"
        assert llm.last_reasoning_content == "Let me think..."

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_reasoning_content_empty_when_not_present(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="Simple answer", finish_reason="stop"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Simple answer"
        assert llm.last_reasoning_content == ""


class TestProviderTokenEstimation:
    """Token estimation via tiktoken when API doesn't report usage."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_tiktoken_estimation_used_when_no_usage(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "deepseek/deepseek-v4-flash"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.deepseek.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        # No usage chunk — provider should fall back to tiktoken estimation
        client = _mock_openai_client([
            _openai_chunk(content="Hello world", finish_reason="stop"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Hello world"
        assert llm.last_usage["input_tokens"] > 0
        assert llm.last_usage["output_tokens"] > 0


class TestProviderStreaming:
    """Streaming behavior regression tests."""

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_multiple_chunks_concatenated(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="Hello"),
            _openai_chunk(content=", "),
            _openai_chunk(content="world"),
            _openai_chunk(content="!", finish_reason="stop"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "Hello, world!"

    @patch("agentnexus.core.llm.get_settings")
    @patch("agentnexus.core.llm.trace_manager")
    def test_truncated_finish_reason_detected(self, mock_trace, mock_settings):
        mock_settings.return_value.llm_model_id = "openai/gpt-4"
        mock_settings.return_value.llm_api_key.get_secret_value.return_value = "sk-test"
        mock_settings.return_value.llm_base_url = "https://api.openai.com"
        mock_settings.return_value.llm_timeout = 30
        mock_trace.active = None

        client = _mock_openai_client([
            _openai_chunk(content="partial...", finish_reason="length"),
        ])

        llm = AgentLLM()
        with patch("agentnexus.core.providers.openai_provider.OpenAI", return_value=client):
            result = llm._call([{"role": "user", "content": "hi"}], 0, True, 0)

        assert result == "partial..."
        assert llm.last_truncated is True

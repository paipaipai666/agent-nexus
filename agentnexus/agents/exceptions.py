class AgentNexusError(Exception):
    """Base exception for all AgentNexus domain errors.

    Subclasses distinguish recoverable vs fatal errors so callers
    can decide whether to retry, degrade gracefully, or propagate.
    """


class LLMError(AgentNexusError):
    """Errors originating from LLM calls (transient or permanent)."""


class LLMTransientError(LLMError):
    """Retryable LLM error (network timeout, rate limit, 503)."""


class LLMPermanentError(LLMError):
    """Non-retryable LLM error (auth failure, invalid model, etc.)."""


class MemoryError(AgentNexusError):
    """Errors in the memory subsystem (STM/LTM/extraction)."""


class RAGError(AgentNexusError):
    """Errors in the RAG retrieval pipeline."""


class MCPConnectionError(AgentNexusError):
    """MCP server connection or protocol error."""


class ToolExecutionError(AgentNexusError):
    """Agent tool execution error."""


class ConfigurationError(AgentNexusError):
    """Missing or invalid configuration."""

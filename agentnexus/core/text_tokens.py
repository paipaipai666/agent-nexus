"""Shared token encoding for text budgets independent of memory implementations."""

from functools import lru_cache


@lru_cache(maxsize=1)
def get_token_encoding():
    """Load the shared cl100k_base encoder on first use."""
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")

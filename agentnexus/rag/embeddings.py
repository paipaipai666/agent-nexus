"""Embedding model access shared by RAG and memory subsystems."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from hashlib import blake2b
from typing import Any

from agentnexus.core.config import get_settings

VECTOR_DIM = 512
_EMBED_BATCH_SIZE = 1024
_EMBED_TORCH_THREADS_CAP = 12

_model = None
_model_name: str | None = None
_model_device: str | None = None


class _FallbackEmbeddingModel:
    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim

    def _encode_one(self, text: str, normalize_embeddings: bool = True) -> list[float]:
        vector = [0.0] * self.dim
        for token in _fallback_tokenize(text):
            index = int.from_bytes(blake2b(token.encode("utf-8"), digest_size=8).digest(), "big") % self.dim
            vector[index] += 1.0
        if normalize_embeddings:
            norm = sum(value * value for value in vector) ** 0.5
            if norm > 0:
                return [value / norm for value in vector]
        return vector

    def encode(
        self,
        texts,
        normalize_embeddings: bool = True,
        batch_size: int | None = None,
        show_progress_bar: bool = False,
    ):
        del batch_size, show_progress_bar
        if isinstance(texts, str):
            return self._encode_one(texts, normalize_embeddings=normalize_embeddings)
        return [self._encode_one(text, normalize_embeddings=normalize_embeddings) for text in texts]


def _fallback_tokenize(text: str) -> list[str]:
    normalized = (text or "").strip().lower()
    if not normalized:
        return []
    raw_tokens = re.findall(r"[\u4e00-\u9fff]+|[a-z0-9_]+", normalized)
    if not raw_tokens:
        return [normalized]

    tokens: list[str] = []
    for token in raw_tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            chars = [char for char in token if char.strip()]
            tokens.extend(chars)
            if len(chars) >= 2:
                tokens.extend("".join(chars[index:index + 2]) for index in range(len(chars) - 1))
            if len(chars) >= 3:
                tokens.extend("".join(chars[index:index + 3]) for index in range(len(chars) - 2))
            continue
        tokens.append(token)
    return tokens or [normalized]


def _resolve_embedding_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"

    if torch.cuda.is_available():
        return "cuda"

    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"

    return "cpu"


def _configure_embedding_runtime(device: str) -> None:
    if device != "cpu":
        return
    try:
        import torch
    except ImportError:
        return

    target_threads = min(max(os.cpu_count() or 1, 1), _EMBED_TORCH_THREADS_CAP)
    if torch.get_num_threads() != target_threads:
        torch.set_num_threads(target_threads)


def resolve_embedding_device() -> str:
    return _resolve_embedding_device()


def configure_embedding_runtime(device: str) -> None:
    _configure_embedding_runtime(device)


def get_embedding_model(
    *,
    settings_provider: Callable[[], Any] = get_settings,
    device_resolver: Callable[[], str] | None = None,
    runtime_configurer: Callable[[str], None] | None = None,
):
    global _model, _model_name, _model_device

    settings = settings_provider()
    resolved_device = (device_resolver or _resolve_embedding_device)()
    (runtime_configurer or _configure_embedding_runtime)(resolved_device)
    if _model is None or _model_name != settings.embedding_model or _model_device != resolved_device:
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(settings.embedding_model, device=resolved_device)
        except Exception:
            _model = _FallbackEmbeddingModel()
        _model_name = settings.embedding_model
        _model_device = resolved_device
    return _model


def embedding_to_list(value):
    return value.tolist() if hasattr(value, "tolist") else value


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_embedding_model()
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=_EMBED_BATCH_SIZE,
        show_progress_bar=False,
    )
    return embedding_to_list(embeddings)


def reset_embedding_model() -> None:
    global _model, _model_name, _model_device
    _model = None
    _model_name = None
    _model_device = None


__all__ = [
    "VECTOR_DIM",
    "_FallbackEmbeddingModel",
    "_configure_embedding_runtime",
    "_fallback_tokenize",
    "_resolve_embedding_device",
    "configure_embedding_runtime",
    "embed_texts",
    "embedding_to_list",
    "get_embedding_model",
    "reset_embedding_model",
    "resolve_embedding_device",
]

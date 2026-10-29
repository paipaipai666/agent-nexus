"""Explicit delivery of the local models used by the knowledge base.

Normal model loading reads a complete local snapshot. Network access happens only
when a KB upload or the user-requested reranker preparation calls this module.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal

from agentnexus.core.config import get_settings

logger = logging.getLogger(__name__)

ModelKind = Literal["embedding", "reranker"]
ProgressCallback = Callable[[str], None]

_DEFAULT_REVISIONS = {
    "BAAI/bge-small-zh-v1.5": "7999e1d3359715c523056ef9478215996d62a620",
    "BAAI/bge-reranker-v2-m3": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
}
_DEFAULT_FILES = {
    "BAAI/bge-small-zh-v1.5": (
        "config.json", "config_sentence_transformers.json", "model.safetensors", "modules.json",
        "README.md", "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json",
        "tokenizer_config.json", "vocab.txt", "1_Pooling/config.json",
    ),
    "BAAI/bge-reranker-v2-m3": (
        "config.json", "model.safetensors", "README.md", "sentencepiece.bpe.model",
        "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
    ),
}
_DEFAULT_BYTES = {
    "BAAI/bge-small-zh-v1.5": 96_405_966,
    "BAAI/bge-reranker-v2-m3": 2_293_259_337,
}


class ModelUnavailable(RuntimeError):
    """A required local model cannot be used without preparation or repair."""


@dataclass(frozen=True)
class ModelSnapshot:
    name: str
    path: str
    identity: str


@dataclass(frozen=True)
class AssetStatus:
    name: str
    state: Literal["missing", "cached", "downloading", "loading", "ready", "failed"]
    message: str
    identity: str | None = None

    def to_dict(self) -> dict:
        return {**asdict(self), "download_bytes": _DEFAULT_BYTES.get(self.name)}


_state_lock = threading.Lock()
_prepare_locks = {"embedding": threading.Lock(), "reranker": threading.Lock()}
_statuses: dict[ModelKind, AssetStatus] = {}
_embedding_thread: threading.Thread | None = None
_reranker_thread: threading.Thread | None = None
_loaded_embedding_model = None
_loaded_embedding_identity: str | None = None


def _model_name(kind: ModelKind) -> str:
    settings = get_settings()
    return settings.embedding_model if kind == "embedding" else settings.reranker_model


def _local_identity(path: Path) -> str:
    """Distinguish changes to a user-provided local model directory."""
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8"))
    for item in sorted(path.rglob("*")):
        if item.is_file():
            stat = item.stat()
            digest.update(str(item.relative_to(path)).encode("utf-8"))
            digest.update(f":{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    return "local:" + digest.hexdigest()


def _download_directory(name: str) -> Path | None:
    revision = _DEFAULT_REVISIONS.get(name)
    if revision is None:
        return None
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
    except ImportError:
        return None

    return Path(HF_HUB_CACHE) / "agentnexus" / name.replace("/", "--") / revision


def cached_snapshot(name: str) -> ModelSnapshot | None:
    local = Path(name).expanduser()
    if local.is_dir():
        resolved = local.resolve()
        return ModelSnapshot(name, str(resolved), _local_identity(resolved))

    downloaded = _download_directory(name)
    if downloaded is not None and all((downloaded / filename).is_file() for filename in _DEFAULT_FILES[name]):
        return ModelSnapshot(name, str(downloaded), f"{name}@{downloaded.name}")

    try:
        from huggingface_hub import snapshot_download

        path_text = snapshot_download(
            name,
            revision=_DEFAULT_REVISIONS.get(name),
            local_files_only=True,
        )
        path = Path(path_text)
    except Exception:
        return None
    required = _DEFAULT_FILES.get(name)
    if required is not None and any(not (path / filename).is_file() for filename in required):
        return None
    return ModelSnapshot(name, path_text, f"{name}@{path.name}")


def _set_status(kind: ModelKind, state: AssetStatus) -> None:
    with _state_lock:
        _statuses[kind] = state


def status(kind: ModelKind) -> AssetStatus:
    name = _model_name(kind)
    with _state_lock:
        current = _statuses.get(kind)
    if current is not None and current.name == name and current.state in {"downloading", "loading", "failed"}:
        return current
    snapshot = cached_snapshot(name)
    if snapshot is None:
        return AssetStatus(name, "missing", "Model has not been downloaded")
    if current is not None and current.name == name and current.state == "ready" and current.identity == snapshot.identity:
        return current
    return AssetStatus(name, "cached", "Cached locally; loaded when needed", snapshot.identity)


def statuses() -> dict[str, dict]:
    return {kind: status(kind).to_dict() for kind in ("embedding", "reranker")}


def _prepare_snapshot(
    kind: ModelKind,
    progress: ProgressCallback | None = None,
    *,
    allow_download: bool = True,
    force_download: bool = False,
) -> ModelSnapshot:
    name = _model_name(kind)
    snapshot = cached_snapshot(name)
    if snapshot is not None and not force_download:
        return snapshot
    if not allow_download:
        raise ModelUnavailable(f"{name} is not cached. Choose Prepare in Knowledge Base or run nexus kb prepare")
    if os.environ.get("HF_HUB_OFFLINE", "").lower() in {"1", "true", "yes"}:
        raise ModelUnavailable("Model is not cached; HF_HUB_OFFLINE prevents downloading")
    if os.environ.get("TRANSFORMERS_OFFLINE", "").lower() in {"1", "true", "yes"}:
        raise ModelUnavailable("Model is not cached; TRANSFORMERS_OFFLINE prevents downloading")

    message = f"Downloading {name}; download time depends on your network connection"
    _set_status(kind, AssetStatus(name, "downloading", message))
    if progress is not None:
        progress(message)
    from huggingface_hub import snapshot_download

    try:
        directory = _download_directory(name)
        snapshot_download(
            name,
            revision=_DEFAULT_REVISIONS.get(name),
            local_files_only=False,
            allow_patterns=list(_DEFAULT_FILES[name]) if name in _DEFAULT_FILES else None,
            force_download=force_download,
            local_dir=str(directory) if directory is not None else None,
        )
    except Exception as exc:
        logger.exception("Could not download %s model %s", kind, name)
        raise ModelUnavailable(f"Could not download {name}. Check your network and disk space, then retry") from exc
    snapshot = cached_snapshot(name)
    if snapshot is None:
        raise ModelUnavailable(f"The downloaded {name} snapshot is incomplete; retry preparation")
    return snapshot


def prepare_embedding(
    progress: ProgressCallback | None = None,
    *,
    allow_download: bool = True,
    force_download: bool = False,
):
    """Return a real embedding model and its immutable snapshot identity."""
    global _loaded_embedding_model, _loaded_embedding_identity
    with _prepare_locks["embedding"]:
        name = _model_name("embedding")
        try:
            snapshot = _prepare_snapshot(
                "embedding", progress, allow_download=allow_download, force_download=force_download,
            )
            if not force_download and _loaded_embedding_model is not None and _loaded_embedding_identity == snapshot.identity:
                _set_status("embedding", AssetStatus(name, "ready", "Embedding model is ready", snapshot.identity))
                return _loaded_embedding_model, snapshot.identity
            message = f"Loading {name}"
            _set_status("embedding", AssetStatus(name, "loading", message))
            if progress is not None:
                progress(message)
            from agentnexus.rag.embeddings import (
                VECTOR_DIM,
                _FallbackEmbeddingModel,
                get_embedding_model,
                reset_embedding_model,
            )

            reset_embedding_model()
            model = get_embedding_model()
            if isinstance(model, _FallbackEmbeddingModel):
                raise ModelUnavailable(f"Could not load {name}. Knowledge Base requires the real embedding model; retry preparation")
            actual_dim = model.get_sentence_embedding_dimension()
            if actual_dim != VECTOR_DIM:
                raise ModelUnavailable(f"{name} has {actual_dim} dimensions; Knowledge Base requires {VECTOR_DIM}")
            _loaded_embedding_model = model
            _loaded_embedding_identity = snapshot.identity
            _set_status("embedding", AssetStatus(name, "ready", "Embedding model is ready", snapshot.identity))
            return model, snapshot.identity
        except Exception as exc:
            message = str(exc) if isinstance(exc, ModelUnavailable) else f"Could not load {name}; retry preparation"
            logger.exception("Embedding model preparation failed")
            _set_status("embedding", AssetStatus(name, "failed", message))
            raise ModelUnavailable(message) from exc


def _prepare_embedding_worker(namespace: str, force_download: bool) -> None:
    try:
        from agentnexus.rag.kb_index import ensure_embedding_index

        model, identity = prepare_embedding(force_download=force_download)
        name = _model_name("embedding")
        _set_status("embedding", AssetStatus(name, "loading", "Rebuilding the existing knowledge index"))
        ensure_embedding_index(namespace, model, identity)
        _set_status("embedding", AssetStatus(name, "ready", "Knowledge model and index are ready", identity))
    except Exception as exc:
        name = _model_name("embedding")
        message = str(exc) if isinstance(exc, ModelUnavailable) else "Could not prepare the knowledge index; retry"
        logger.exception("Knowledge base model preparation failed")
        _set_status("embedding", AssetStatus(name, "failed", message))


def start_embedding_prepare(namespace: str) -> AssetStatus:
    """Prepare an existing KB without requiring a new document upload."""
    global _embedding_thread
    with _state_lock:
        if _embedding_thread is not None and _embedding_thread.is_alive():
            return _statuses["embedding"]
        name = _model_name("embedding")
        _statuses["embedding"] = AssetStatus(name, "loading", "Preparing the knowledge embedding model")
        _embedding_thread = threading.Thread(
            target=_prepare_embedding_worker,
            args=(namespace, False),
            daemon=True,
            name="embedding-prepare",
        )
        _embedding_thread.start()
        return _statuses["embedding"]


def _prepare_reranker_worker(force_download: bool) -> None:
    with _prepare_locks["reranker"]:
        name = _model_name("reranker")
        try:
            snapshot = _prepare_snapshot("reranker", force_download=force_download)
            _set_status("reranker", AssetStatus(name, "loading", f"Loading {name}"))
            from agentnexus.rag.retriever import HybridRetriever, reset_reranker_cache

            reset_reranker_cache()
            retriever = HybridRetriever()
            retriever.load_reranker()
            if retriever._reranker is None:
                raise ModelUnavailable(f"Could not load {name}; retry preparation")
            _set_status("reranker", AssetStatus(name, "ready", "Enhanced ranking model is ready", snapshot.identity))
        except Exception as exc:
            message = str(exc) if isinstance(exc, ModelUnavailable) else f"Could not download or load {name}; retry preparation"
            logger.exception("Reranker model preparation failed")
            _set_status("reranker", AssetStatus(name, "failed", message))


def start_reranker_prepare() -> AssetStatus:
    """Start one user-requested reranker transfer; retry after a failure is safe."""
    global _reranker_thread
    with _state_lock:
        if _reranker_thread is not None and _reranker_thread.is_alive():
            return _statuses["reranker"]
        name = _model_name("reranker")
        _statuses["reranker"] = AssetStatus(name, "downloading", "Preparing the enhanced ranking model")
        _reranker_thread = threading.Thread(
            target=_prepare_reranker_worker,
            args=(False,),
            daemon=True,
            name="reranker-prepare",
        )
        _reranker_thread.start()
        return _statuses["reranker"]


def report_load_failure(kind: ModelKind, name: str) -> None:
    """Keep a cached-but-unloadable model visible as a repairable failure."""
    if name == _model_name(kind):
        _set_status(kind, AssetStatus(name, "failed", f"Could not load {name}; retry preparation"))

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _normalize_source_uri(source_uri: str) -> str:
    return source_uri.strip().replace("\\", "/")


def _stable_payload(*parts: str) -> str:
    return "\x1f".join(parts)


def _digest(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}{digest}"


def _stable_metadata(metadata: Mapping[str, Any] | None) -> str:
    if not metadata:
        return ""
    return json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_source_id(source_uri: str) -> str:
    normalized = _normalize_source_uri(source_uri)
    return _digest("src_", normalized)


def make_document_version(
    source_id: str,
    content: str,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    payload = _stable_payload(source_id, content, _stable_metadata(metadata))
    return _digest("doc_", payload)


def make_chunk_id(
    document_version: str,
    chunk_index: int,
    text: str,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    payload = _stable_payload(document_version, str(chunk_index), text, _stable_metadata(metadata))
    return _digest("chunk_", payload)

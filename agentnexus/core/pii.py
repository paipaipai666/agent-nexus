"""PII detection and masking helpers."""

from __future__ import annotations

import re

_PII_PATTERNS = [
    re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
    re.compile(r"1[3-9]\d{9}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"\b\d{15,19}\b"),
]


def contains_pii(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PII_PATTERNS)


def mask_pii(text: str) -> str:
    """Partially mask PII while preserving enough structure for downstream use."""
    if not text:
        return text

    def _mask_email(match):
        domain = match.group(2)
        tld = domain.rsplit(".", 1)
        masked = re.sub(r"[^.]", "*", tld[0])
        return match.group(1) + "***@" + masked + "." + tld[1]

    text = re.sub(r"([\w.-])[\w.-]+@([\w.-]+\.\w+)", _mask_email, text)
    text = re.sub(
        r"(1[3-9]\d)\d{4}(\d{4})\b",
        lambda match: match.group(1) + "****" + match.group(2),
        text,
    )
    text = re.sub(
        r"(sk-)[A-Za-z0-9]{32,}",
        lambda match: match.group(1)
        + "*" * min(8, len(match.group(0)) - 3)
        + ("..." if len(match.group(0)) > 11 else ""),
        text,
    )
    text = re.sub(
        r"\b(\d{4})\d{7,11}(\d{4})\b",
        lambda match: match.group(1) + "****" + match.group(2),
        text,
    )
    return text


# Backward-compatible private names used by older tests/imports.
_contains_pii = contains_pii
_mask_pii = mask_pii

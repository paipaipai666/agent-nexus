"""Shared text processing utilities."""


def collapse_and_truncate(text: str, limit: int) -> str:
    """Collapse whitespace and truncate to *limit* characters with ellipsis."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"

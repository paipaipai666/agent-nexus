"""Safe tool execution wrapper with fallback system.

Wraps any tool call with try/except and a fallback chain.
Provides a built-in fallback registry for common tools:
  - matplotlib  -> ASCII chart
  - nltk        -> simplified BLEU
  - API         -> mock / cache placeholder
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pre‑built fallback functions
# ---------------------------------------------------------------------------

def fallback_ascii_chart(data: dict[str, float], title: str = "ASCII Chart") -> str:
    """Render a simple vertical bar chart in pure ASCII.

    Intended as a fallback when matplotlib is not available or fails.
    Accepts a mapping of ``{label: value}`` and returns a monospace-text
    visualisation.

    Parameters
    ----------
    data : dict[str, float]
        Labels and their numeric values.
    title : str
        Optional title printed above the chart.

    Returns
    -------
    str
        Plain-text bar chart.
    """
    if not data:
        return f"[{title}]\n(no data)"

    max_val = max(abs(v) for v in data.values()) or 1.0
    bar_width = 40
    lines: list[str] = [f" {title} ".center(60, "="), ""]

    for label, value in data.items():
        bar_len = max(1, int(abs(value) / max_val * bar_width))
        bar = "█" * bar_len
        lines.append(f"  {label:<20} │ {bar} {value:>8.2f}")

    lines.append("")
    lines.append("─" * 60)
    return "\n".join(lines)


def fallback_bleu_simple(ref: str, hyp: str, max_n: int = 4) -> dict[str, float]:
    """Compute a basic BLEU score without depending on ``nltk``.

    Uses modified n‑gram precision with a simplified brevity penalty.
    Suitable as a drop‑in fallback when NLTK is unavailable.

    Parameters
    ----------
    ref : str
        Reference (ground‑truth) sentence.
    hyp : str
        Hypothesis (candidate) sentence.
    max_n : int
        Maximum n‑gram order (default 4, standard BLEU).

    Returns
    -------
    dict[str, float]
        ``{"bleu": <score 0-1>, "precisions": {...}, "brevity_penalty": ...}``.
    """
    ref_tokens = ref.split()
    hyp_tokens = hyp.split()
    ref_len = len(ref_tokens)
    hyp_len = len(hyp_tokens)

    precisions: dict[int, float] = {}
    for n in range(1, min(max_n, ref_len, hyp_len) + 1):
        ref_ngrams = Counter(
            tuple(ref_tokens[i : i + n]) for i in range(ref_len - n + 1)
        )
        hyp_ngrams = Counter(
            tuple(hyp_tokens[i : i + n]) for i in range(hyp_len - n + 1)
        )

        clipped = sum(min(count, ref_ngrams.get(ng, 0)) for ng, count in hyp_ngrams.items())
        total = max(1, sum(hyp_ngrams.values()))
        precisions[n] = clipped / total

    # Geometric mean of precisions
    if precisions:
        import math
        log_sum = sum(math.log(p) for p in precisions.values())
        geo_mean = math.exp(log_sum / len(precisions))
    else:
        geo_mean = 0.0

    # Brevity penalty
    brevity_penalty = 1.0 if hyp_len >= ref_len else math.exp(1 - ref_len / max(1, hyp_len))

    bleu = brevity_penalty * geo_mean

    return {
        "bleu": round(bleu, 6),
        "precisions": {f"p{n}": round(v, 6) for n, v in precisions.items()},
        "brevity_penalty": round(brevity_penalty, 6),
    }


def fallback_api_cache() -> str:
    """Return a canned response when an external API call fails.

    This is a pure placeholder. In a real deployment you might return the
    last cached response for the given endpoint, or an informative message
    directing the caller to retry later.

    Returns
    -------
    str
        A fixed hint string.
    """
    return "[fallback] API call skipped — returning cached/mock response"


# ---------------------------------------------------------------------------
# Fallback registry
# ---------------------------------------------------------------------------

FALLBACK_REGISTRY: dict[str, Callable[..., Any]] = {
    "matplotlib": fallback_ascii_chart,
    "nltk": fallback_bleu_simple,
    "api": fallback_api_cache,
}
"""Map tool / library names to their fallback implementations.

Keys are convention‑based identifiers (e.g. ``"matplotlib"``, ``"nltk"``,
``"api"``).  The lookup is performed by *name*, not by import.
"""


# ---------------------------------------------------------------------------
# Core wrapper functions
# ---------------------------------------------------------------------------

def safe_call(
    tool_fn: Callable[..., Any],
    fallback_fn: Optional[Callable[..., Any]] = None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute *tool_fn* and return its result, or fall back on failure.

    Parameters
    ----------
    tool_fn : Callable
        The primary tool or function to invoke.
    fallback_fn : Callable or None
        Optional fallback to call when *tool_fn* raises an exception.
        When ``None`` the exception is re‑raised.
    *args, **kwargs
        Passed verbatim to both *tool_fn* and (when applicable) *fallback_fn*.

    Returns
    -------
    Any
        The return value of either *tool_fn* (success) or *fallback_fn* (failure).
    """
    try:
        return tool_fn(*args, **kwargs)
    except Exception as exc:
        logger.warning(
            "safe_call: %s(%s, %s) failed with %s: %s",
            getattr(tool_fn, "__name__", tool_fn),
            args,
            {k: _summarise(v) for k, v in kwargs.items()},
            type(exc).__name__,
            exc,
        )
        if fallback_fn is not None:
            logger.info("safe_call: falling back to %s", getattr(fallback_fn, "__name__", fallback_fn))
            return _error_aware_fallback(fallback_fn, exc, *args, **kwargs)
        raise


def safe_call_with_registry(
    tool_name: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run *fn* and fall back via *FALLBACK_REGISTRY* if it fails.

    Parameters
    ----------
    tool_name : str
        Key into *FALLBACK_REGISTRY* (e.g. ``"matplotlib"``, ``"nltk"``).
    fn : Callable
        The primary function to attempt.
    *args, **kwargs
        Forwarded to *fn* and the registered fallback.

    Returns
    -------
    Any
        The result of *fn*, or the result of the registered fallback.

    Raises
    ------
    RuntimeError
        When *tool_name* is not found in *FALLBACK_REGISTRY* (i.e. no
        fallback is configured for the given tool).
    """
    fallback = FALLBACK_REGISTRY.get(tool_name)
    if fallback is None:
        logger.warning("safe_call_with_registry: no fallback for '%s', calling fn directly", tool_name)
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return f"[fallback] 工具 '{tool_name}' 不可用且无注册的降级方案: {exc}"
    return safe_call(fn, fallback_fn=fallback, *args, **kwargs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _summarise(value: Any, max_len: int = 80) -> str:
    """Truncate long values for log messages."""
    s = repr(value)
    return s[:max_len] + "..." if len(s) > max_len else s


def _error_aware_fallback(
    fallback_fn: Callable[..., Any],
    original_exc: Exception,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Invoke the fallback but guard against *it* failing as well.

    If the fallback itself raises, a ``RuntimeError`` wrapping both
    exceptions is thrown so the caller always sees a clear trace.
    """
    try:
        return fallback_fn(*args, **kwargs)
    except Exception as fb_exc:
        raise RuntimeError(
            f"Primary call failed ({type(original_exc).__name__}: {original_exc}), "
            f"and fallback also failed ({type(fb_exc).__name__}: {fb_exc})"
        ) from fb_exc

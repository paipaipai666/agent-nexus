"""Shared helpers for eval CLI commands."""
from importlib.util import find_spec
from urllib.parse import urlparse

from agentnexus.cli import console
from agentnexus.core.config import get_settings


def _fmt_ci(score: float, ci: tuple | None = None) -> str:
    if ci and len(ci) == 2:
        return f"{score:.3f} [{ci[0]:.2f}-{ci[1]:.2f}]"
    return f"{score:.3f}"


def _fmt_pct(score: float) -> str:
    return f"{score:.1%}"


def _text_setting(value, default: str = "unknown") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _endpoint_mode(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").casefold()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "local"
    if host:
        return "remote"
    return "unknown"


def _detect_embedding_device() -> str:
    try:
        from agentnexus.rag.embeddings import resolve_embedding_device

        return resolve_embedding_device()
    except Exception:
        return "cpu"


def _collect_eval_runtime_summary() -> list[str]:
    from agentnexus.cli import eval_cmd

    settings = getattr(eval_cmd, "get_settings", get_settings)()
    embedding_model = _text_setting(getattr(settings, "embedding_model", None))
    reranker_model = _text_setting(getattr(settings, "reranker_model", None))
    llm_model = _text_setting(getattr(settings, "llm_model_id", None))
    llm_base = _text_setting(getattr(settings, "llm_base_url", None))
    judge_model = _text_setting(getattr(settings, "judge_model_id", None))
    judge_base = _text_setting(getattr(settings, "judge_base_url", None))

    embedding_backend = "fallback-hash"
    spec_finder = getattr(eval_cmd, "find_spec", find_spec)
    if spec_finder("sentence_transformers") is not None:
        embedding_backend = "sentence-transformers"

    device_detector = getattr(eval_cmd, "_detect_embedding_device", _detect_embedding_device)
    device = device_detector()

    gpu_enabled = embedding_backend == "sentence-transformers" and device in {"cuda", "mps"}
    gpu_label = "yes" if gpu_enabled else "no"

    return [
        f"Embedding: {embedding_model} | backend={embedding_backend} | device={device} | GPU={gpu_label}",
        "Dense retrieval: enabled",
        "Hybrid retrieval: BM25 + dense embeddings",
        f"Reranker: disabled in `nexus eval run` (configured model: {reranker_model})",
        f"Generator LLM: {llm_model} | endpoint={_endpoint_mode(llm_base)} | base={llm_base}",
        f"Judge LLM: {judge_model} | endpoint={_endpoint_mode(judge_base)} | base={judge_base}",
        "Query rewrite / multi-query / HyDE: not used by current `eval run` path",
    ]


def _print_eval_runtime_summary(target_console=None) -> None:
    output_console = target_console or console
    output_console.print("[bold]评估运行信息:[/bold]")
    for line in _collect_eval_runtime_summary():
        output_console.print(f"  - {line}")
    output_console.print()



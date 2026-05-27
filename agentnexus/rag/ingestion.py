from __future__ import annotations

from collections.abc import Callable

from agentnexus.prompts import load_prompt

from .chunking import ChunkStrategy, chunk_structured_document
from .chunking import chunk_text as _chunk_text
from .loaders import clean_text, load_document, load_structured_document
from .models import IngestedDocument

CONTEXTUAL_RETRIEVAL_PROMPT = load_prompt("contextual_retrieval")
CONTEXTUAL_GENERATION_PROMPT = load_prompt("contextual_generation")
chunk_text = _chunk_text


def load_and_clean(file_path: str) -> str:
    return clean_text(load_document(file_path))


def ingest_document(
    file_path: str,
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    enable_contextual: bool = False,
    llm_client=None,
) -> IngestedDocument:
    document = load_structured_document(file_path)
    chunks = chunk_structured_document(
        document,
        strategy=strategy,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    if enable_contextual and llm_client and chunks:
        enriched_pairs = enrich_chunks_with_context(
            [chunk.indexed_text or chunk.text for chunk in chunks],
            document.indexed_text or document.raw_text or document.content,
            llm_client,
        )
        for chunk, enriched in zip(chunks, enriched_pairs, strict=False):
            retrieval_text = enriched["retrieval"]
            generation_text = enriched["generation"]
            chunk.text = generation_text
            chunk.indexed_text = retrieval_text
            chunk.sparse_text = retrieval_text
            chunk.metadata["generation_text"] = generation_text
            chunk.metadata["retrieval_text"] = retrieval_text

    return IngestedDocument(document=document, chunks=chunks)


def ingest(
    file_path: str,
    strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    enable_contextual: bool = False,
    llm_client=None,
) -> list[str]:
    artifacts = ingest_document(
        file_path,
        strategy=strategy,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        enable_contextual=enable_contextual,
        llm_client=llm_client,
    )
    return artifacts.legacy_chunks()


def generate_chunk_context(document: str, chunk: str, llm_client, *, mode: str) -> str:
    prompt = _contextual_prompt_for_mode(mode).format(document=document, chunk=chunk)
    try:
        response = llm_client.think(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            silent=True,
        )
        return response.strip() if response else ""
    except Exception:
        return ""


def enrich_chunks_with_context(
    chunks: list[str],
    document: str,
    llm_client,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, str]]:
    """Enrich chunks with context for retrieval and generation.

    Args:
        chunks: List of text chunks to enrich
        document: Full document text
        llm_client: LLM client for generating context
        progress_callback: Optional callback(current, total) for progress reporting
    """
    enriched: list[dict[str, str]] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(i + 1, total)
        retrieval_context = generate_chunk_context(document, chunk, llm_client, mode="retrieval")
        generation_context = generate_chunk_context(document, chunk, llm_client, mode="generation")
        enriched.append(
            {
                "retrieval": _merge_context_and_chunk(retrieval_context, chunk),
                "generation": _merge_context_and_chunk(generation_context, chunk),
            }
        )
    return enriched


def _contextual_prompt_for_mode(mode: str) -> str:
    if mode == "retrieval":
        return CONTEXTUAL_RETRIEVAL_PROMPT
    if mode == "generation":
        return CONTEXTUAL_GENERATION_PROMPT
    raise ValueError(f"未知的 contextual 模式: {mode}")


def _merge_context_and_chunk(context: str, chunk: str) -> str:
    normalized_context = (context or "").strip()
    normalized_chunk = (chunk or "").strip()
    if normalized_context and normalized_chunk:
        return f"{normalized_context}\n\n{normalized_chunk}"
    return normalized_context or normalized_chunk

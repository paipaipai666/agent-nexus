from __future__ import annotations

from agentnexus.prompts import load_prompt

from .chunking import ChunkStrategy, chunk_structured_document
from .chunking import chunk_text as _chunk_text
from .loaders import clean_text, load_document, load_structured_document
from .models import IngestedDocument

CONTEXTUAL_PROMPT = load_prompt("contextual")
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
        enriched_texts = enrich_chunks_with_context(
            [chunk.indexed_text or chunk.text for chunk in chunks],
            document.indexed_text or document.raw_text or document.content,
            llm_client,
        )
        for chunk, enriched_text in zip(chunks, enriched_texts, strict=False):
            chunk.text = enriched_text
            chunk.indexed_text = enriched_text
            chunk.sparse_text = enriched_text

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


def generate_chunk_context(document: str, chunk: str, llm_client) -> str:
    prompt = CONTEXTUAL_PROMPT.format(document=document, chunk=chunk)
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
) -> list[str]:
    from rich.progress import track

    enriched = []
    for chunk in track(chunks, description="生成上下文摘要..."):
        context = generate_chunk_context(document, chunk, llm_client)
        if context:
            enriched.append(f"{context}\n\n{chunk}")
        else:
            enriched.append(chunk)
    return enriched

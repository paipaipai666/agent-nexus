"""Code entity embedding generation.

Generates embedding text for code entities using the build_embedding_text
function from models.py, then uses the shared embedding model to produce vectors.

Reuses the existing sentence-transformers infrastructure from rag/embeddings.py.
"""

from __future__ import annotations

from agentnexus.codegraph.models import NodeData, build_embedding_text
from agentnexus.rag.embeddings import embed_texts, embedding_to_list, get_embedding_model


def generate_embedding(node: NodeData) -> list[float] | None:
    """Generate embedding vector for a single code entity.

    Returns None for variable/import nodes (no embedding).
    """
    text = build_embedding_text(node)
    if not text:
        return None
    model = get_embedding_model()
    vec = model.encode(text, normalize_embeddings=True)
    return embedding_to_list(vec)


def generate_embeddings_batch(nodes: list[NodeData]) -> list[list[float] | None]:
    """Generate embeddings for a batch of nodes.

    Returns a list parallel to the input nodes. Elements are None
    for nodes that don't need embeddings (variable/import).
    """
    texts = [build_embedding_text(n) for n in nodes]

    # Track which indices need embedding
    to_embed_indices: list[int] = []
    to_embed_texts: list[str] = []
    for i, text in enumerate(texts):
        if text:
            to_embed_indices.append(i)
            to_embed_texts.append(text)

    if not to_embed_texts:
        return [None] * len(nodes)

    # Generate embeddings in batch
    vectors = embed_texts(to_embed_texts)

    # Build result list
    result: list[list[float] | None] = [None] * len(nodes)
    for i, vec in zip(to_embed_indices, vectors):
        result[i] = vec
    return result


__all__ = ["generate_embedding", "generate_embeddings_batch"]

"""Mechanical verification of wiki statements against source chunks.

Uses Jaccard similarity (string overlap) and cosine similarity (embedding distance)
to verify or correct LLM-assigned synthesis levels. No LLM calls in this module —
all checks are deterministic and reproducible.

IMPORTANT: Cosine similarity thresholds are calibrated against a specific embedding
model (default: BAAI/bge-small-zh-v1.5). Changing the embedding model requires
re-running calibration (`nexus wiki calibrate`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agentnexus.core.config import get_settings
from agentnexus.rag import embeddings as embedding_service

from .models import SynthesisLevel, WikiStatement

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> set[str]:
    """Split text into word/character tokens for Jaccard computation.

    Handles mixed Chinese/English: Chinese characters become individual tokens,
    English words stay together. Punctuation is stripped.
    """
    # Remove punctuation, normalize whitespace
    cleaned = re.sub(r"[^\w\s一-鿿]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    tokens: set[str] = set()
    for segment in cleaned.split():
        # Check if segment contains Chinese characters
        has_chinese = bool(re.search(r"[一-鿿]", segment))
        if has_chinese:
            # Split Chinese characters individually, keep English words together
            buf = []
            for ch in segment:
                if "一" <= ch <= "鿿":
                    if buf:
                        tokens.add("".join(buf).lower())
                        buf = []
                    tokens.add(ch)
                else:
                    buf.append(ch)
            if buf:
                tokens.add("".join(buf).lower())
        else:
            tokens.add(segment.lower())
    return tokens


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """Compute Jaccard similarity between two texts.

    Returns 0.0-1.0 where 1.0 means identical token sets.
    """
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def cosine_similarity(text_a: str, text_b: str) -> float:
    """Compute cosine similarity between two texts using the configured embedding model.

    Returns 0.0-1.0 where 1.0 means identical vectors.
    Thresholds are calibrated against the embedding model in settings — changing
    the model requires re-running `nexus wiki calibrate`.
    """
    vecs = embedding_service.embed_texts([text_a, text_b])
    if len(vecs) < 2:
        return 0.0
    # Dot product of normalized vectors = cosine similarity
    return sum(a * b for a, b in zip(vecs[0], vecs[1]))


class MechanicalVerifier:
    """Verifies wiki statements against source chunks using mechanical checks.

    Each verify call returns the corrected synthesis_level. The original
    LLM-assigned level is never trusted without verification.
    """

    def __init__(self, thresholds: dict[str, float] | None = None):
        settings = get_settings()
        t = thresholds or {}
        self.jaccard_direct_quote = t.get("jaccard_direct_quote", settings.wiki_jaccard_direct_quote)
        self.jaccard_paraphrase = t.get("jaccard_paraphrase", settings.wiki_jaccard_paraphrase)
        self.cosine_paraphrase = t.get("cosine_paraphrase", settings.wiki_cosine_paraphrase)
        self.cosine_source = t.get("cosine_source", settings.wiki_cosine_source)

    def verify_statement(
        self,
        statement: WikiStatement,
        chunk_texts: dict[str, str],
    ) -> str:
        """Verify a statement's synthesis level against its source chunks.

        Args:
            statement: The wiki statement to verify.
            chunk_texts: Map of chunk_id -> chunk text for all referenced chunks.

        Returns:
            The verified synthesis level (one of SynthesisLevel values).
        """
        if not statement.source_chunk_ids:
            # No source chunks — must be synthesis
            return SynthesisLevel.SYNTHESIS.value

        assigned = statement.synthesis_level
        primary_chunk_id = statement.source_chunk_ids[0]
        primary_text = chunk_texts.get(primary_chunk_id, "")

        if not primary_text:
            logger.warning(f"Chunk {primary_chunk_id} not found in chunk_texts, marking as synthesis")
            return SynthesisLevel.SYNTHESIS.value

        # Step 1: For direct_quote and paraphrase, check string overlap with primary chunk
        if assigned in (SynthesisLevel.DIRECT_QUOTE.value, SynthesisLevel.PARAPHRASE.value):
            return self._verify_single_source(statement.text, primary_text)

        # Step 2: For cross_reference, verify each source chunk individually
        if assigned == SynthesisLevel.CROSS_REFERENCE.value:
            return self._verify_multi_source(statement.text, statement.source_chunk_ids, chunk_texts)

        # Step 3: synthesis stays synthesis — nothing to verify
        return SynthesisLevel.SYNTHESIS.value

    def _verify_single_source(self, statement_text: str, chunk_text: str) -> str:
        """Verify a statement claimed to be direct_quote or paraphrase."""
        # First check Jaccard (cheap, no model loading)
        jac = jaccard_similarity(statement_text, chunk_text)
        if jac >= self.jaccard_direct_quote:
            return SynthesisLevel.DIRECT_QUOTE.value
        if jac >= self.jaccard_paraphrase:
            # Jaccard suggests paraphrase, confirm with cosine
            cos = cosine_similarity(statement_text, chunk_text)
            if cos >= self.cosine_paraphrase:
                return SynthesisLevel.PARAPHRASE.value
            # Jaccard high but cosine low — likely shared vocabulary but different meaning
            return SynthesisLevel.CROSS_REFERENCE.value

        # Jaccard low — check cosine for semantic similarity
        cos = cosine_similarity(statement_text, chunk_text)
        if cos >= self.cosine_paraphrase:
            return SynthesisLevel.PARAPHRASE.value

        # Both low — not well-supported by this chunk
        return SynthesisLevel.SYNTHESIS.value

    def _verify_multi_source(
        self,
        statement_text: str,
        source_chunk_ids: list[str],
        chunk_texts: dict[str, str],
    ) -> str:
        """Verify a cross_reference statement by checking each source chunk."""
        valid_chunks: list[str] = []

        for chunk_id in source_chunk_ids:
            chunk_text = chunk_texts.get(chunk_id, "")
            if not chunk_text:
                continue
            cos = cosine_similarity(statement_text, chunk_text)
            if cos >= self.cosine_source:
                valid_chunks.append(chunk_id)
            else:
                logger.debug(
                    f"Chunk {chunk_id} below source threshold "
                    f"(cosine={cos:.3f} < {self.cosine_source}),剔除"
                )

        if len(valid_chunks) == 0:
            return SynthesisLevel.SYNTHESIS.value
        if len(valid_chunks) == 1:
            # Only one valid source — reclassify as paraphrase
            return self._verify_single_source(
                statement_text, chunk_texts.get(valid_chunks[0], "")
            )
        return SynthesisLevel.CROSS_REFERENCE.value

    def verify_and_update_statement(
        self,
        statement: WikiStatement,
        chunk_texts: dict[str, str],
    ) -> tuple[str, bool]:
        """Verify and return (new_level, changed).

        Returns the verified level and whether it differs from the original.
        """
        new_level = self.verify_statement(statement, chunk_texts)
        old_level = statement.verified_synthesis_level or statement.synthesis_level
        return new_level, new_level != old_level

"""Text normalization, tokenization, and fuzzy matching."""

from __future__ import annotations

import re
import unicodedata

from agentnexus.skills.router.types import (
    _ABBREVIATION_MAP,
    _STOPWORDS,
    _TOKEN_RE,
)


def tokenize(text: str) -> list[str]:
    """Tokenize text with support for Chinese and English."""
    tokens: list[str] = []
    normalized = unicodedata.normalize(
        "NFKC", split_mixed_script_boundaries((text or "").lower()),
    )

    try:
        import jieba
        for token in jieba.cut(normalized):
            token = token.strip()
            if len(token) < 1 or token in _STOPWORDS:
                continue
            if (
                len(token) == 1
                and "一" <= token <= "鿿"
                and token not in {"写", "查", "找", "读", "改", "转"}
            ):
                continue
            tokens.append(token)
        for token in augment_tokens_with_known_phrases(normalized, tokens):
            if token not in tokens:
                tokens.append(token)
        return tokens
    except ImportError:
        pass

    for raw in _TOKEN_RE.findall(normalized):
        for part in re.split(r"[_\-]+", raw):
            token = part.strip()
            if len(token) < 2 and token not in {"写", "查", "找", "读", "改", "转"}:
                continue
            if token in _STOPWORDS:
                continue
            tokens.append(token)
    for token in augment_tokens_with_known_phrases(normalized, tokens):
        if token not in tokens:
            tokens.append(token)
    return tokens


def split_mixed_script_boundaries(text: str) -> str:
    text = re.sub(r"([一-鿿])([a-z0-9])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([一-鿿])", r"\1 \2", text)
    return text


def augment_tokens_with_known_phrases(
    normalized: str, tokens: list[str],
) -> list[str]:
    """Add known phrase-level tokens that tokenizers may miss."""
    extras: list[str] = []
    token_set = set(tokens)
    phrase_map = {
        "serach": ["search"],
        "검색": ["search"],
        "検索": ["search"],
        "情報": ["信息", "search"],
        "备份": ["备份"],
        "数据库": ["数据库"],
        "代理服务器": ["代理", "服务器"],
        "网络代理": ["代理", "网络"],
        "读取": ["读取"],
        "配置": ["配置"],
        "修改": ["修改"],
        "编写": ["编写"],
        "发送": ["发送"],
        "邮件": ["邮件"],
        "源码": ["源码"],
        "仓库": ["仓库"],
        "洞察": ["洞察"],
        "报告": ["报告"],
        "excel": ["excel"],
        "spreadshet": ["spreadsheet"],
    }
    for phrase, additions in phrase_map.items():
        if phrase.lower() in normalized:
            for addition in additions:
                if addition not in token_set and addition not in extras:
                    extras.append(addition)
    return extras


# ── Fuzzy Matching ──────────────────────────────────────────────────


def levenshtein_distance(s: str, t: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if s == t:
        return 0
    if not s:
        return len(t)
    if not t:
        return len(s)
    if abs(len(s) - len(t)) > 2:
        return abs(len(s) - len(t))

    rows = len(s) + 1
    cols = len(t) + 1
    prev = list(range(cols))
    curr = [0] * cols

    for i in range(1, rows):
        curr[0] = i
        for j in range(1, cols):
            cost = 0 if s[i - 1] == t[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + cost,
            )
        prev, curr = curr, prev
    return prev[cols - 1]


def fuzzy_match_term(
    term: str, candidates: tuple[str, ...],
) -> tuple[str | None, float]:
    """Find the best fuzzy match for a term among candidates.

    Returns (matched_term, similarity_ratio) or (None, 0.0).
    """
    best_term: str | None = None
    best_ratio = 0.0

    for cand in candidates:
        if cand == term:
            return cand, 1.0

        # Edit distance based match
        max_len = max(len(term), len(cand))
        if max_len == 0:
            continue
        dist = levenshtein_distance(term, cand)
        if dist <= 2:
            ratio = 1.0 - (dist / max_len)
            if ratio >= 0.7 and ratio > best_ratio:
                best_term = cand
                best_ratio = ratio

        # Prefix match (for abbreviations like doc→docx)
        if len(term) >= 2 and cand.startswith(term) and len(cand) <= len(term) + 4:
            ratio = len(term) / len(cand)
            if ratio > best_ratio:
                best_term = cand
                best_ratio = ratio

        # Reverse prefix (term contains candidate)
        if len(cand) >= 3 and term.startswith(cand) and len(term) <= len(cand) + 4:
            ratio = len(cand) / len(term)
            if ratio > best_ratio:
                best_term = cand
                best_ratio = ratio

    # Try abbreviation expansion
    if best_ratio < 0.8:
        expanded = _ABBREVIATION_MAP.get(term)
        if expanded:
            for cand in candidates:
                if expanded in cand or cand in expanded:
                    ratio = min(len(expanded), len(cand)) / max(
                        len(expanded), len(cand),
                    )
                    if ratio > best_ratio:
                        best_term = cand
                        best_ratio = max(ratio, 0.75)

    return best_term, best_ratio

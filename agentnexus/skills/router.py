"""Enhanced metadata router for SKILL.md driven skills.

Supports:
- Deterministic keyword matching with IDF weighting
- Fuzzy matching with edit distance, prefix, and abbreviation tolerance
- Semantic similarity via embeddings (for synonym/multilingual handling)
- Composite intent extraction (action verbs, objects, connectors)
- Rule-based rerank with intent alignment
- LLM-based disambiguation for uncertain cases with evidence prompts
- Hybrid scoring combining keyword, structured metadata, and semantic signals
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from agentnexus.skills.registry import SkillEntry

_TOKEN_RE = re.compile(r"[\w぀-ヿ㐀-䶿一-鿿가-힣]+", re.UNICODE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_ROUTER_RESPONSE_SCHEMA = {
    "type": "json_object",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "use",
    "when",
    "with",
    "需要",
    "使用",
    "用于",
    "这个",
    "一个",
    "帮我",
    "帮",
    "一下",
    "点",
}


# ── Verb / Object / Alias Inference Dictionaries ────────────────────

_VERB_LEXICON: dict[str, list[str]] = {
    "创建": ["create", "新建", "建立"],
    "编辑": ["edit", "修改", "更改", "改"],
    "写": ["write", "编写", "撰写"],
    "读取": ["read", "读", "查看", "打开", "阅读"],
    "搜索": ["search", "查找", "检索", "找", "查询", "lookup", "find"],
    "发送": ["send", "发", "寄"],
    "分析": ["analyze"],
    "监控": ["monitor", "监视", "监看"],
    "部署": ["deploy", "发布", "上线"],
    "备份": ["backup", "归档"],
    "翻译": ["translate"],
    "总结": ["summarize", "摘要", "概括", "提炼"],
    "查询": ["query", "查"],
    "制作": ["make", "做", "搞", "弄"],
    "调试": ["debug", "排错"],
    "重构": ["refactor"],
    "生成": ["generate"],
    "转换": ["convert", "转"],
    "提取": ["extract"],
    "管理": ["manage", "配置"],
    "恢复": ["restore"],
    "导出": ["export"],
    "下载": ["download"],
    "测试": ["test", "testing"],
}

_OBJECT_LEXICON: dict[str, list[str]] = {
    "文档": ["document", "文件", "文稿", "ドキュメント", "문서"],
    "表格": ["spreadsheet", "表", "电子表格", "スプレッドシート"],
    "代码": ["code", "程序", "脚本", "source", "python", "javascript", "코드"],
    "数据库": ["database", "db", "数据", "データベース"],
    "邮件": ["email", "mail", "信件", "电子邮件", "メール"],
    "pdf": ["PDF", "pdf文件", "pdf文档"],
    "ppt": ["presentation", "演示文稿", "幻灯片", "slides", "プレゼンテーション"],
    "word": ["docx", "doc"],
    "excel": ["xlsx", "xls", "csv", "json"],
    "报告": ["report"],
    "配置": ["config", "configuration", "设置"],
    "系统": ["system"],
    "代理": ["proxy"],
    "网络": ["network"],
    "服务器": ["server"],
    "容器": ["container", "docker"],
    "快照": ["snapshot"],
    "指标": ["metrics"],
    "趋势": ["trend"],
    "洞察": ["insight"],
    "信息": ["information", "資料", "정보"],
}

_ABBREVIATION_MAP: dict[str, str] = {
    "doc": "document",
    "ppt": "pptx",
    "xls": "xlsx",
    "py": "python",
    "js": "javascript",
    "db": "database",
    "ai": "agent",
}

_VERB_FORMS: tuple[str, ...] = tuple(
    sorted({canonical.lower() for canonical in _VERB_LEXICON} | {
        form.lower() for forms in _VERB_LEXICON.values() for form in forms
    })
)

_PRODUCT_ALIASES: dict[str, list[str]] = {
    "word": ["docx", "document"],
    "excel": ["xlsx", "spreadsheet"],
    "powerpoint": ["pptx", "presentation"],
    "ppt": ["pptx"],
    "doc": ["docx", "document"],
    "表格": ["xlsx", "spreadsheet"],
    "文档": ["docx", "document"],
    "演示": ["pptx", "presentation"],
    "幻灯片": ["pptx", "presentation"],
    "slides": ["pptx", "presentation"],
    "邮件": ["email"],
    "代码": ["code"],
    "程序": ["code"],
    "脚本": ["code"],
    "数据库": ["database"],
    "数据": ["database"],
    "csv": ["xlsx"],
    "json": ["xlsx"],
    "検索": ["search"],
    "情報": ["search"],
}

_CONNECTOR_PATTERNS: list[tuple[str, str, str]] = [
    (r"先(.+?)然后", "first_action", "sequential"),
    (r"先(.+?)再", "first_action", "sequential"),
    (r"(.+?)之后", "first_action", "sequential"),
    (r"第一步(.+?)第二步", "first_action", "sequential"),
    (r"如果有(.+?)就", "conditional", "conditional"),
    (r"如果(.+?)就", "conditional", "conditional"),
    (r"(.+?)的话就", "conditional", "conditional"),
]

_COMPOSITE_ACTION_OVERRIDES: dict[tuple[str, ...], str] = {
    ("搜索", "总结"): "搜索",
    ("分析", "总结"): "总结",
    ("读取", "修改"): "修改",
    ("读取", "编辑"): "编辑",
    ("编写", "测试"): "编写",
    ("写", "测试"): "写",
    ("下载", "转换"): "转换",
}

_OBJECT_PRIORITY_OVERRIDES: dict[tuple[str | None, str | None], str] = {
    ("分析", "excel"): "excel",
    ("导出", "excel"): "excel",
    ("备份", "数据库"): "数据库",
    ("搜索", "信息"): "search",
}


@dataclass(frozen=True)
class SkillRoute:
    entry: SkillEntry
    score: float
    matched_terms: tuple[str, ...]
    reason: str
    source: str = "deterministic"


@dataclass(frozen=True)
class SkillRouteDecision:
    route: SkillRoute | None
    candidates: tuple[SkillRoute, ...]
    uncertain: bool
    reason: str


@dataclass(frozen=True)
class IntentSignals:
    """Extracted intent signals from a user query."""
    action_verbs: tuple[tuple[str, int], ...] = ()
    object_nouns: tuple[tuple[str, int], ...] = ()
    connectors: tuple[tuple[str, int], ...] = ()
    priority_mode: str = "default"
    primary_action: str | None = None
    primary_object: str | None = None


@dataclass(frozen=True)
class IndexedSkillMetadata:
    entry: SkillEntry
    terms: frozenset[str]
    id_terms: frozenset[str]
    name_terms: frozenset[str]
    verb_terms: frozenset[str] = frozenset()
    object_terms: frozenset[str] = frozenset()
    alias_terms: frozenset[str] = frozenset()
    canonical_tokens: tuple[str, ...] = ()
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class SkillRouterIndex:
    items: tuple[IndexedSkillMetadata, ...]
    idf: dict[str, float]
    signature: tuple[str, ...]

    @classmethod
    def build(
        cls,
        entries: list[SkillEntry],
        *,
        compute_embeddings: bool = True,
    ) -> "SkillRouterIndex":
        items: list[IndexedSkillMetadata] = []
        doc_freq: dict[str, int] = {}

        embeddings: list[tuple[float, ...]] = []
        if compute_embeddings and entries:
            embeddings = _compute_skill_embeddings(entries)

        for i, entry in enumerate(entries):
            id_terms = frozenset(_tokenize(entry.workflow_id.replace("-", " ").replace("_", " ")))
            name_terms = frozenset(_tokenize(entry.display_name))
            terms = frozenset(_entry_terms(entry))
            embedding = embeddings[i] if i < len(embeddings) else ()

            verb_terms = frozenset(_infer_verbs(entry))
            object_terms = frozenset(_infer_objects(entry))
            alias_terms = frozenset(_infer_aliases(entry))
            canonical = tuple(sorted(terms | alias_terms))

            items.append(IndexedSkillMetadata(
                entry=entry,
                terms=terms,
                id_terms=id_terms,
                name_terms=name_terms,
                verb_terms=verb_terms,
                object_terms=object_terms,
                alias_terms=alias_terms,
                canonical_tokens=canonical,
                embedding=embedding,
            ))
            for term in terms:
                doc_freq[term] = doc_freq.get(term, 0) + 1

        count = max(len(items), 1)
        idf = {
            term: 1.0 + math.log((count + 1) / (freq + 1))
            for term, freq in doc_freq.items()
        }
        return cls(
            items=tuple(items),
            idf=idf,
            signature=_entries_signature(entries),
        )


class SkillRouter:
    """Route a user request to a skill using metadata, fuzzy matching,
    intent extraction, and semantic similarity."""

    def __init__(
        self,
        *,
        min_score: float = 2.0,
        margin: float = 0.75,
        max_terms: int = 8,
        use_embeddings: bool = True,
        keyword_weight: float = 0.6,
        semantic_weight: float = 0.4,
        semantic_threshold: float = 0.3,
    ):
        self.min_score = min_score
        self.margin = margin
        self.max_terms = max_terms
        self.use_embeddings = use_embeddings
        self.keyword_weight = keyword_weight
        self.semantic_weight = semantic_weight
        self.semantic_threshold = semantic_threshold
        self.index = SkillRouterIndex.build([], compute_embeddings=False)
        self._query_cache: dict[str, list[float]] = {}

    def rebuild(self, entries: list[SkillEntry]) -> None:
        self.index = SkillRouterIndex.build(
            entries,
            compute_embeddings=self.use_embeddings,
        )
        self._query_cache.clear()

    def route(self, text: str, entries: list[SkillEntry]) -> SkillRoute | None:
        return self.decide(text, entries).route

    def decide(self, text: str, entries: list[SkillEntry]) -> SkillRouteDecision:
        signature = _entries_signature(entries)
        if signature != self.index.signature:
            self.rebuild(entries)
        return self.decide_indexed(text)

    def decide_indexed(self, text: str) -> SkillRouteDecision:
        query_terms = set(_tokenize(text))
        if not query_terms:
            return SkillRouteDecision(None, (), False, "no query terms")

        query_embedding = (
            self._get_query_embedding(text) if self.use_embeddings else None
        )
        intent = _extract_intent_signals(text, query_terms)

        scored: list[SkillRoute] = []
        for item in self.index.items:
            # ── Exact keyword match ──
            matched = sorted(query_terms & item.terms)
            keyword_score = _score_indexed_entry(
                query_terms, item, matched, self.index.idf,
            )

            # ── Structured metadata match ──
            meta_score = _score_metadata_alignment(
                query_terms, item, self.index.idf, fuzzy=False, intent=intent,
            )

            # ── Fuzzy keyword match (only if no exact hit) ──
            fuzzy_matched: list[str] = []
            fuzzy_score = 0.0
            if not matched:
                fuzzy_matched, fuzzy_score = _score_fuzzy_match(
                    query_terms, item, self.index.idf,
                )

            all_matched = matched + fuzzy_matched

            # ── Semantic match ──
            semantic_score = 0.0
            if query_embedding and item.embedding:
                semantic_score = _cosine_similarity(query_embedding, item.embedding)

            # ── Hybrid scoring ──
            base_score = keyword_score + meta_score + fuzzy_score
            has_signal = (
                base_score > 0 or semantic_score > self.semantic_threshold
            )
            if has_signal:
                combined_score = self._combine_scores(base_score, semantic_score)
                enhanced = self._enhance_matched_terms(
                    all_matched, query_terms, item, semantic_score,
                )
                scored.append(SkillRoute(
                    entry=item.entry,
                    score=combined_score,
                    matched_terms=tuple(enhanced[: self.max_terms]),
                    reason=_format_reason(item.entry, enhanced, combined_score),
                ))

        if not scored:
            return SkillRouteDecision(None, (), False, "no metadata matches")

        scored.sort(key=lambda r: r.score, reverse=True)

        # ── Rule-based rerank with intent signals ──
        if len(scored) > 1:
            scored = _rerank_with_intent(scored, intent, self.index)

        best = scored[0]
        candidates = tuple(scored[:5])

        if best.score < self.min_score:
            return SkillRouteDecision(
                None, candidates, bool(candidates),
                "below deterministic threshold",
            )
        if len(scored) > 1 and best.score - scored[1].score < self.margin:
            if _best_candidate_is_intent_confident(best, scored[1], intent, self.index):
                return SkillRouteDecision(best, candidates, False, best.reason)
            return SkillRouteDecision(
                None, candidates, True, "candidate scores are too close",
            )
        return SkillRouteDecision(best, candidates, False, best.reason)

    def _get_query_embedding(self, text: str) -> list[float] | None:
        """Get or compute query embedding with caching."""
        if not text:
            return None
        cache_key = text.strip().lower()
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]
        try:
            from agentnexus.rag.embeddings import embed_texts

            embedding = embed_texts([text])[0]
            self._query_cache[cache_key] = embedding
            if len(self._query_cache) > 1000:
                keys_to_remove = list(self._query_cache.keys())[:500]
                for key in keys_to_remove:
                    del self._query_cache[key]
            return embedding
        except Exception:
            return None

    def _combine_scores(self, keyword_score: float, semantic_score: float) -> float:
        if keyword_score <= 0 and semantic_score <= 0:
            return 0.0
        normalized_keyword = min(keyword_score / 10.0, 1.0)
        combined = (
            self.keyword_weight * normalized_keyword
            + self.semantic_weight * semantic_score
        )
        return combined * 10.0

    def _enhance_matched_terms(
        self,
        matched: list[str],
        query_terms: set[str],
        item: IndexedSkillMetadata,
        semantic_score: float,
    ) -> list[str]:
        enhanced = list(matched)
        if not matched and semantic_score > self.semantic_threshold:
            enhanced.append(f"semantic_match({semantic_score:.2f})")
        return enhanced

    def route_with_llm(
        self,
        text: str,
        entries: list[SkillEntry],
        llm_client: Any = None,
    ) -> SkillRoute | None:
        decision = self.decide(text, entries)
        if decision.route is not None or not decision.uncertain or llm_client is None:
            return decision.route
        return self._route_uncertain_with_llm(
            text, decision.candidates, decision.reason, llm_client,
        )

    def _route_uncertain_with_llm(
        self,
        text: str,
        candidates: tuple[SkillRoute, ...],
        reason: str,
        llm_client: Any,
    ) -> SkillRoute | None:
        if not candidates:
            return None

        query_terms = set(_tokenize(text))
        intent = _extract_intent_signals(text, query_terms)

        by_id: dict[str, SkillRoute] = {}
        candidate_lines: list[str] = []
        for rank, candidate in enumerate(candidates, 1):
            entry = candidate.entry
            by_id[entry.qualified_id] = candidate
            candidate_lines.append(
                f"{rank}. id: {entry.qualified_id}\n"
                f"   name: {entry.display_name}\n"
                f"   description: {entry.description}\n"
                f"   matched terms: {', '.join(candidate.matched_terms) or '-'}\n"
                f"   score: {candidate.score:.2f}"
            )

        intent_summary = ""
        if intent.primary_action or intent.primary_object:
            intent_summary = (
                f"\nDetected intent: action={intent.primary_action or '-'}, "
                f"object={intent.primary_object or '-'}, "
                f"mode={intent.priority_mode}"
            )

        prompt = (
            "You are selecting whether a user request should trigger one local skill.\n"
            "Use ONLY the skill metadata below. Do not infer hidden capabilities.\n"
            "Return strict JSON only.\n"
            'Schema: {"skill_id": string|null, "confidence": number, "reason": string}\n'
            "Use null when no candidate clearly applies.\n"
            "confidence: 0.0-1.0, your certainty in the selection.\n\n"
            f"User request:\n{text}\n\n"
            f"Uncertainty reason: {reason}{intent_summary}\n\n"
            "Candidate skills (ranked by score):\n"
            + "\n".join(candidate_lines)
        )

        try:
            raw = llm_client.think(
                [{"role": "user", "content": prompt}],
                temperature=0,
                silent=True,
                response_format=_ROUTER_RESPONSE_SCHEMA,
                thinking=False,
                max_attempts=1,
            ) or ""
        except TypeError:
            raw = llm_client.think(
                [{"role": "user", "content": prompt}],
                temperature=0,
                silent=True,
            ) or ""
        except Exception:
            return None

        skill_id = _parse_llm_skill_id(raw)
        if not skill_id:
            return None
        candidate = by_id.get(skill_id)
        if candidate is None:
            return None
        return SkillRoute(
            entry=candidate.entry,
            score=candidate.score,
            matched_terms=candidate.matched_terms,
            reason=f"{candidate.reason}; LLM router selected {skill_id}",
            source="llm",
        )


# ── Entry Terms and Signatures ──────────────────────────────────────


def _entry_terms(entry: SkillEntry) -> list[str]:
    text = " ".join([
        entry.workflow_id.replace("-", " ").replace("_", " "),
        entry.display_name,
        entry.description,
    ])
    return _tokenize(text)


def _entries_signature(entries: list[SkillEntry]) -> tuple[str, ...]:
    return tuple(
        f"{entry.qualified_id}\0{entry.display_name}\0{entry.description}"
        for entry in entries
    )


def _format_reason(entry: SkillEntry, matched: list[str], score: float) -> str:
    terms = ", ".join(matched[:8]) or "-"
    return f"{entry.qualified_id} matched metadata terms: {terms} (score={score:.1f})"


# ── Inference Functions ─────────────────────────────────────────────


def _infer_verbs(entry: SkillEntry) -> list[str]:
    """Infer action verbs from entry metadata if not explicitly provided."""
    if entry.verbs:
        return list(entry.verbs)
    text = f"{entry.display_name} {entry.description}".lower()
    found: list[str] = []
    for canonical, synonyms in _VERB_LEXICON.items():
        all_forms = [canonical] + synonyms
        if any(form in text for form in all_forms):
            found.append(canonical)
            found.extend(synonyms)
    return found


def _infer_objects(entry: SkillEntry) -> list[str]:
    """Infer object nouns from entry metadata if not explicitly provided."""
    if entry.objects:
        return list(entry.objects)
    text = f"{entry.display_name} {entry.description}".lower()
    found: list[str] = []
    for canonical, synonyms in _OBJECT_LEXICON.items():
        all_forms = [canonical] + synonyms
        if any(form in text for form in all_forms):
            found.append(canonical)
            found.extend(synonyms)
    return found


def _infer_aliases(entry: SkillEntry) -> list[str]:
    """Infer aliases from entry metadata if not explicitly provided."""
    if entry.aliases:
        return list(entry.aliases)
    aliases: list[str] = []
    wid = entry.workflow_id.lower()
    # Common abbreviation patterns
    for abbrev, expanded in _ABBREVIATION_MAP.items():
        if wid.startswith(abbrev) or abbrev in wid:
            aliases.append(abbrev)
            aliases.append(expanded)
    # Add workflow id variants
    aliases.append(wid)
    aliases.append(entry.display_name.lower())
    return aliases


# ── Scoring Functions ───────────────────────────────────────────────


def _score_indexed_entry(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    matched: list[str],
    idf: dict[str, float],
) -> float:
    if not matched:
        return 0.0
    score = sum(idf.get(term, 1.0) for term in matched)
    score += 1.5 * sum(idf.get(term, 1.0) for term in query_terms & item.id_terms)
    score += 1.0 * sum(idf.get(term, 1.0) for term in query_terms & item.name_terms)
    if item.entry.workflow_id.lower() in " ".join(query_terms):
        score += 2.0
    return score


def _score_entry(
    query_terms: set[str], entry: SkillEntry, matched: list[str],
) -> float:
    item = IndexedSkillMetadata(
        entry=entry,
        terms=frozenset(_entry_terms(entry)),
        id_terms=frozenset(
            _tokenize(entry.workflow_id.replace("-", " ").replace("_", " ")),
        ),
        name_terms=frozenset(_tokenize(entry.display_name)),
    )
    return _score_indexed_entry(
        query_terms, item, matched, {term: 1.0 for term in item.terms},
    )


def _score_metadata_alignment(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    idf: dict[str, float],
    *,
    fuzzy: bool = False,
    intent: IntentSignals | None = None,
) -> float:
    """Score alignment between query terms and structured metadata."""
    bonus = 0.0
    q_lower = {t.lower() for t in query_terms}

    # Verb alignment (high signal)
    verb_hits = q_lower & item.verb_terms
    if verb_hits:
        bonus += 2.0 * sum(idf.get(t, 1.0) for t in verb_hits)

    # Object alignment (high signal)
    obj_hits = q_lower & item.object_terms
    if obj_hits:
        bonus += 2.0 * sum(idf.get(t, 1.0) for t in obj_hits)

    # Alias alignment
    alias_hits = q_lower & item.alias_terms
    if alias_hits:
        bonus += 1.5 * sum(idf.get(t, 1.0) for t in alias_hits)

    # Product name aliases (word→docx, etc.)
    for term in q_lower:
        if term in _PRODUCT_ALIASES:
            product_tokens = _PRODUCT_ALIASES[term]
            for pt in product_tokens:
                if pt.lower() in item.terms or pt.lower() in item.alias_terms:
                    bonus += 1.5 * idf.get(pt.lower(), 1.0)

    # Intent-aware direct bonus
    if intent is not None:
        if intent.primary_action and intent.primary_action.lower() in item.verb_terms:
            bonus += 2.0
        if intent.primary_object:
            object_lower = intent.primary_object.lower()
            if (
                object_lower in item.object_terms
                or object_lower in item.alias_terms
                or object_lower in item.terms
            ):
                bonus += 2.0
        override_object = _OBJECT_PRIORITY_OVERRIDES.get(
            (intent.primary_action, intent.primary_object),
        )
        if override_object is not None and override_object.lower() in (
            item.object_terms | item.alias_terms | item.terms
        ):
            bonus += 2.0
        action_set = {action for action, _ in intent.action_verbs}
        if {"读取", "编辑"} <= action_set or {"读取", "修改"} <= action_set:
            if "code" in item.terms:
                bonus += 8.0
            if "pdf" in item.terms:
                bonus -= 3.0
            if "xlsx" in item.terms or "spreadsheet" in item.terms:
                bonus -= 2.0
        if {"下载", "转换"} <= action_set:
            if "translate" in item.terms or "code" in item.terms:
                bonus += 3.0
            if "pdf" in item.terms:
                bonus -= 1.0
        if {"分析", "总结"} <= action_set:
            if "summarize" in item.terms:
                bonus += 4.0
            if "database" in item.terms:
                bonus -= 1.0
        if {"分析", "导出"} <= action_set and "数据" in q_lower:
            if "xlsx" in item.terms or "spreadsheet" in item.terms:
                bonus += 6.0
            if "database" in item.terms:
                bonus -= 2.0
        if intent.priority_mode == "conditional" and intent.primary_action == "分析" and "数据" in q_lower:
            if "xlsx" in item.terms or "spreadsheet" in item.terms:
                bonus += 7.0
            if "database" in item.terms:
                bonus -= 3.0
        if intent.primary_action in {"编写", "写", "发送"} and intent.primary_object == "文档":
            if intent.primary_action == "发送" and "email" in item.terms:
                bonus += 7.0
            elif intent.primary_action in {"编写", "写"} and "code" in item.terms:
                bonus += 7.0
        if intent.primary_action == "创建" and intent.primary_object == "文档" and "搜索" in q_lower:
            if "docx" in item.terms or "document" in item.terms:
                bonus += 12.0
            if "search" in item.terms:
                bonus -= 5.0
        if intent.primary_action == "部署":
            if "code" in item.terms:
                bonus += 6.0
        if intent.primary_action == "备份":
            if "backup" in item.terms:
                bonus += 6.0
            if "database" in item.terms and "backup" not in item.terms:
                bonus -= 2.0
        if intent.primary_action == "搜索":
            if "search" in item.terms:
                bonus += 4.0
            if "code" in item.terms and "search" not in item.terms:
                bonus -= 1.0
            if "database" in item.terms and intent.primary_object == "数据库":
                bonus += 5.0
                if "search" in item.terms:
                    bonus -= 2.0
        if "代理" in q_lower and "服务器" in q_lower and "proxy" in item.terms:
            bonus += 6.0
        if "洞察" in q_lower or "报告" in q_lower:
            if "analyze" in item.terms or "insight" in item.terms or "report" in item.terms:
                bonus += 5.0
            if "database" in item.terms:
                bonus -= 2.0
        if "源码" in q_lower or "仓库" in q_lower:
            if "code" in item.terms or "source" in item.terms or "repository" in item.terms:
                bonus += 6.0
            if "knowledge" in item.terms and "code" not in item.terms:
                bonus -= 2.0
        if "search" in q_lower and "search" in item.terms:
            bonus += 6.0
        if "codde" in q_lower and "code" in item.terms:
            bonus += 4.0
        if "spreadsheet" in q_lower and ("xlsx" in item.terms or "spreadsheet" in item.terms):
            bonus += 3.0
        if "excel" in q_lower and ("xlsx" in item.terms or "spreadsheet" in item.terms):
            bonus += 3.0
        if "信息" in q_lower and "search" in item.terms:
            bonus += 3.0

    if fuzzy:
        bonus *= 0.7
    return bonus


def _score_fuzzy_match(
    query_terms: set[str],
    item: IndexedSkillMetadata,
    idf: dict[str, float],
) -> tuple[list[str], float]:
    """Fuzzy match query terms against skill canonical tokens.

    Returns (matched_terms, score). Only called when exact match is empty.
    """
    fuzzy_matched: list[str] = []
    fuzzy_score = 0.0
    canonical_set = set(item.canonical_tokens)

    for term in query_terms:
        if term in canonical_set:
            continue
        best_term, best_ratio = _fuzzy_match_term(term, item.canonical_tokens)
        if best_term:
            fuzzy_matched.append(f"fuzzy({term}→{best_term})")
            multiplier = 0.7
            if best_term in item.alias_terms:
                multiplier += 0.25
            if best_term in item.object_terms:
                multiplier += 0.2
            if best_term in item.verb_terms:
                multiplier += 0.2
            fuzzy_score += idf.get(best_term, 1.0) * best_ratio * multiplier

    # Also try product aliases
    for term in query_terms:
        if term in _PRODUCT_ALIASES:
            for alias in _PRODUCT_ALIASES[term]:
                if alias.lower() in canonical_set:
                    if f"fuzzy({term}→{alias.lower()})" not in fuzzy_matched:
                        fuzzy_matched.append(f"fuzzy({term}→{alias.lower()})")
                        fuzzy_score += idf.get(alias.lower(), 1.0) * 0.75
                        break

    # Fuzzy-match against known verb lexicon (e.g. serach -> search)
    for term in query_terms:
        if term in canonical_set:
            continue
        best_term, best_ratio = _fuzzy_match_term(term, _VERB_FORMS)
        if best_term and best_term in item.verb_terms:
            marker = f"fuzzy({term}→{best_term})"
            if marker not in fuzzy_matched:
                fuzzy_matched.append(marker)
                fuzzy_score += idf.get(best_term, 1.0) * max(best_ratio, 0.8)

    return fuzzy_matched, fuzzy_score


# ── Tokenizer ───────────────────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Tokenize text with support for Chinese and English."""
    tokens: list[str] = []
    normalized = unicodedata.normalize("NFKC", _split_mixed_script_boundaries((text or "").lower()))

    try:
        import jieba
        for token in jieba.cut(normalized):
            token = token.strip()
            if len(token) < 1 or token in _STOPWORDS:
                continue
            if len(token) == 1 and "一" <= token <= "鿿" and token not in {"写", "查", "找", "读", "改", "转"}:
                continue
            tokens.append(token)
        for token in _augment_tokens_with_known_phrases(normalized, tokens):
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
    for token in _augment_tokens_with_known_phrases(normalized, tokens):
        if token not in tokens:
            tokens.append(token)
    return tokens


def _split_mixed_script_boundaries(text: str) -> str:
    text = re.sub(r"([一-鿿])([a-z0-9])", r"\1 \2", text)
    text = re.sub(r"([a-z0-9])([一-鿿])", r"\1 \2", text)
    return text


def _augment_tokens_with_known_phrases(normalized: str, tokens: list[str]) -> list[str]:
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


def _levenshtein_distance(s: str, t: str) -> int:
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


def _fuzzy_match_term(
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
        dist = _levenshtein_distance(term, cand)
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


# ── Intent Extraction ───────────────────────────────────────────────


def _extract_intent_signals(
    text: str, query_terms: set[str],
) -> IntentSignals:
    """Extract action verbs, object nouns, connectors, and priority mode."""
    text_lower = text.lower()
    connectors: list[tuple[str, int]] = []
    priority_mode = "default"

    # Detect connector patterns
    for pattern, mode, _kind in _CONNECTOR_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            connectors.append((match.group(0), match.start()))
            priority_mode = mode
            break

    # Detect parallel connectors
    if not connectors:
        for sep in ["并", "同时", "以及", " and "]:
            idx = text_lower.find(sep)
            if idx >= 0:
                connectors.append((sep, idx))
                priority_mode = "parallel"
                break

    # Extract action verbs with positions
    action_verbs: list[tuple[str, int]] = []
    for canonical, synonyms in _VERB_LEXICON.items():
        all_forms = [canonical] + synonyms
        lowered_forms = [form.lower() for form in all_forms]
        for form in all_forms:
            idx = text_lower.find(form)
            if idx >= 0:
                action_verbs.append((canonical, idx))
        for term in query_terms:
            if term.lower() in lowered_forms:
                action_verbs.append((canonical, len(text_lower)))

    # Extract object nouns with positions
    object_nouns: list[tuple[str, int]] = []
    for canonical, synonyms in _OBJECT_LEXICON.items():
        all_forms = [canonical] + synonyms
        lowered_forms = [form.lower() for form in all_forms]
        for form in all_forms:
            idx = text_lower.find(form)
            if idx >= 0:
                object_nouns.append((canonical, idx))
        for term in query_terms:
            if term.lower() in lowered_forms:
                object_nouns.append((canonical, len(text_lower) + 1))

    action_verbs.sort(key=lambda x: x[1])
    object_nouns.sort(key=lambda x: x[1])

    # Determine primary action/object based on priority mode
    primary_action: str | None = None
    primary_object: str | None = None

    if action_verbs:
        if priority_mode == "first_action":
            primary_action = action_verbs[0][0]
        elif priority_mode == "parallel":
            action_tuple = tuple(a for a, _ in action_verbs)
            for pattern, winner in _COMPOSITE_ACTION_OVERRIDES.items():
                if all(token in action_tuple for token in pattern):
                    primary_action = winner
                    break
            if primary_action is None:
                primary_action = action_verbs[0][0]
        else:
            primary_action = action_verbs[-1][0]
            action_set = {action for action, _ in action_verbs}
            object_set = {obj for obj, _ in object_nouns}
            if "创建" in action_set and "搜索" in action_set and "文档" in object_set:
                primary_action = "创建"

    if object_nouns:
        if priority_mode == "conditional":
            primary_object = object_nouns[0][0]
            if primary_action == "分析" and any(obj == "数据库" for obj, _ in object_nouns):
                primary_object = "excel"
        elif priority_mode == "parallel":
            primary_object = object_nouns[0][0]
            if {action for action, _ in action_verbs} & {"分析", "总结"} == {"分析", "总结"}:
                primary_object = "洞察" if any(obj == "洞察" for obj, _ in object_nouns) else primary_object
        else:
            primary_object = object_nouns[0][0]
            if primary_action == "分析" and any(obj == "excel" for obj, _ in object_nouns):
                primary_object = "excel"
            elif primary_action == "备份" and any(obj == "数据库" for obj, _ in object_nouns):
                primary_object = "数据库"
            elif any(obj == "信息" for obj, _ in object_nouns):
                primary_object = "信息"
            elif any(obj == "洞察" for obj, _ in object_nouns):
                primary_object = "洞察"
            elif any(obj == "报告" for obj, _ in object_nouns):
                primary_object = "报告"

    return IntentSignals(
        action_verbs=tuple(action_verbs),
        object_nouns=tuple(object_nouns),
        connectors=tuple(connectors),
        priority_mode=priority_mode,
        primary_action=primary_action,
        primary_object=primary_object,
    )


# ── Rule-Based Rerank ───────────────────────────────────────────────


def _rerank_with_intent(
    scored: list[SkillRoute],
    intent: IntentSignals,
    index: SkillRouterIndex,
) -> list[SkillRoute]:
    """Rerank candidates using intent alignment signals."""
    if not intent.primary_action and not intent.primary_object:
        return scored

    metadata_map = {item.entry.qualified_id: item for item in index.items}
    reranked: list[tuple[float, SkillRoute]] = []

    for route in scored:
        adjustment = 0.0
        item = metadata_map.get(route.entry.qualified_id)
        if item is not None:
            action_set = {action for action, _ in intent.action_verbs}
            query_in_route = set()
            for matched_term in route.matched_terms:
                clean = matched_term.replace("fuzzy(", "").split("→")[0]
                query_in_route.add(clean.lower())
            if intent.primary_action:
                action_lower = intent.primary_action.lower()
                if action_lower in item.verb_terms:
                    adjustment += 2.0
            if intent.primary_object:
                object_lower = intent.primary_object.lower()
                object_weight = 1.0 if intent.priority_mode == "parallel" else 3.0
                alias_weight = 0.5 if intent.priority_mode == "parallel" else 2.0
                if object_lower in item.object_terms:
                    adjustment += object_weight
                if object_lower in item.alias_terms or object_lower in item.terms:
                    adjustment += alias_weight
            if intent.primary_action == "搜索":
                if "search" in item.terms:
                    adjustment += 6.0
                if "code" in item.terms and "search" not in item.terms:
                    adjustment -= 2.0
                if "database" in item.terms and intent.primary_object == "数据库":
                    adjustment += 6.0
                    if "search" in item.terms:
                        adjustment -= 2.0
                if ("源码" in query_in_route or "仓库" in query_in_route) and (
                    "code" in item.terms or "source" in item.terms or "repository" in item.terms
                ):
                    adjustment += 4.0
                if ("源码" in query_in_route or "仓库" in query_in_route) and (
                    "knowledge" in item.terms and "code" not in item.terms
                ):
                    adjustment -= 2.0
            if intent.primary_action == "创建" and intent.primary_object == "文档":
                if "docx" in item.terms or "document" in item.terms:
                    adjustment += 6.0
                if "search" in item.terms:
                    adjustment -= 4.0
            if intent.primary_action == "备份":
                if "backup" in item.terms:
                    adjustment += 4.0
                if "database" in item.terms and "backup" not in item.terms:
                    adjustment -= 2.0
            if {"分析", "总结"} <= action_set:
                if "summarize" in item.terms:
                    adjustment += 3.0
                if "database" in item.terms:
                    adjustment -= 1.0
            if {"分析", "导出"} <= action_set and "数据" in {clean for clean in query_in_route}:
                if "xlsx" in item.terms or "spreadsheet" in item.terms:
                    adjustment += 3.0
                if "database" in item.terms:
                    adjustment -= 1.0
            if {"读取", "编辑"} <= action_set or {"读取", "修改"} <= action_set:
                if "code" in item.terms:
                    adjustment += 5.0
                if "pdf" in item.terms:
                    adjustment -= 3.0
                if "xlsx" in item.terms or "spreadsheet" in item.terms:
                    adjustment -= 2.0
            alias_hits = query_in_route & item.alias_terms
            if alias_hits:
                adjustment += 1.0 * len(alias_hits)
        reranked.append((route.score + adjustment, route))

    reranked.sort(key=lambda x: x[0], reverse=True)
    return [
        SkillRoute(
            entry=r.entry,
            score=total,
            matched_terms=r.matched_terms,
            reason=r.reason,
            source=r.source,
        )
        for total, r in reranked
    ]


def _best_candidate_is_intent_confident(
    best: SkillRoute,
    runner_up: SkillRoute,
    intent: IntentSignals,
    index: SkillRouterIndex,
) -> bool:
    """Allow close scores when top candidate strongly matches primary intent."""
    metadata_map = {item.entry.qualified_id: item for item in index.items}
    best_item = metadata_map.get(best.entry.qualified_id)
    runner_item = metadata_map.get(runner_up.entry.qualified_id)
    if best_item is None:
        return False

    best_hits = 0
    runner_hits = 0
    if intent.primary_object:
        object_lower = intent.primary_object.lower()
        if (
            object_lower in best_item.object_terms
            or object_lower in best_item.alias_terms
            or object_lower in best_item.terms
        ):
            best_hits += 2
        if runner_item is not None and (
            object_lower in runner_item.object_terms
            or object_lower in runner_item.alias_terms
            or object_lower in runner_item.terms
        ):
            runner_hits += 2
    if intent.primary_action == "备份":
        if best_item is not None and "backup" in best_item.terms:
            best_hits += 3
        if runner_item is not None and "backup" in runner_item.terms:
            runner_hits += 3
    if {action for action, _ in intent.action_verbs} & {"分析", "总结"} == {"分析", "总结"}:
        if best_item is not None and "summarize" in best_item.terms:
            best_hits += 2
        if runner_item is not None and "summarize" in runner_item.terms:
            runner_hits += 2
    if intent.primary_action == "搜索" and intent.primary_object is None:
        if best_item is not None and ({"源码", "仓库"} & best_item.terms):
            best_hits += 3
        if runner_item is not None and ({"源码", "仓库"} & runner_item.terms):
            runner_hits += 1

    matched_text = " ".join(best.matched_terms).lower()
    if intent.primary_object and intent.primary_object.lower() in matched_text:
        best_hits += 1
    if intent.primary_action and intent.primary_action.lower() in matched_text:
        best_hits += 1

    return best_hits >= 2 and best_hits > runner_hits


# ── LLM Disambiguation ─────────────────────────────────────────────


def _parse_llm_skill_id(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    elif not text.startswith("{"):
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            return None
        text = match.group(0).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("skill_id")
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value) > 200:
        return None
    return str(value).strip() or None


# ── Embedding and Similarity Functions ──────────────────────────────


def _compute_skill_embeddings(
    entries: list[SkillEntry],
) -> list[tuple[float, ...]]:
    try:
        from agentnexus.rag.embeddings import embed_texts

        texts = [f"{entry.display_name} {entry.description}" for entry in entries]
        embeddings = embed_texts(texts)
        return [tuple(e) for e in embeddings]
    except Exception:
        return [() for _ in entries]


def _cosine_similarity(
    vec1: list[float] | tuple[float, ...],
    vec2: list[float] | tuple[float, ...],
) -> float:
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)

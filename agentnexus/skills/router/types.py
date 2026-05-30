"""Router v2 type definitions, constants, and lexicons."""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentnexus.skills.registry import SkillEntry

_TOKEN_RE = re.compile(r"[\w぀-ヿ一-鿿가-힯]+", re.UNICODE)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_ROUTER_RESPONSE_SCHEMA = {"type": "json_object"}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "for", "in", "is", "it",
    "of", "on", "or", "the", "to", "use", "when", "with",
    "需要", "使用", "用于", "这个", "一个", "帮我", "帮", "一下", "点",
}

# ── Verb / Object / Alias Inference Dictionaries ────────────────────

_VERB_LEXICON: dict[str, list[str]] = {
    "创建": ["create", "新建", "建立"],
    "编辑": ["edit", "修改", "更改", "改"],
    "写": ["write", "编写", "撰写", "書く", "작성"],
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
    "信息": ["information", "資料", "情報"],
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
    "csv": ["csv", "data"],
    "json": ["json", "data"],
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

# NOTE: Composite action overrides are a global heuristic. When a query contains
# multiple verbs, this table picks which verb should drive skill selection.
# A future improvement would allow per-skill override metadata so individual
# skills can declare their own composite-action preferences.
_COMPOSITE_ACTION_OVERRIDES: dict[tuple[str, ...], str] = {
    ("搜索", "总结"): "搜索",
    ("分析", "总结"): "总结",
    ("读取", "修改"): "修改",
    ("读取", "编辑"): "编辑",
    ("编写", "测试"): "编写",
    ("写", "测试"): "写",
    ("下载", "转换"): "转换",
}


# ── Data Classes ────────────────────────────────────────────────────


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
    mode: str = "single"  # single / multi_intent / ambiguous / abstain
    confidence: float = 0.0
    secondary_skills: tuple[str, ...] = ()


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
    domain_terms: frozenset[str] = frozenset()
    example_texts: tuple[str, ...] = ()
    example_terms: frozenset[str] = frozenset()
    negative_hint_terms: frozenset[str] = frozenset()
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
        from agentnexus.skills.router.retrieve import build_index
        return build_index(entries, compute_embeddings=compute_embeddings)

"""Performance tests for skill router with complex scenarios.

These tests measure the router's performance with:
- Synonym matching
- Multilingual queries
- Disambiguation scenarios
- Fuzzy matching
- Composite intents

Thresholds:
    - Synonym lookup latency < 10ms per query
    - Multilingual accuracy > 70%
    - Disambiguation accuracy > 65%
    - Fuzzy matching accuracy > 60%
"""

from __future__ import annotations

import time
from pathlib import Path

from agentnexus.services.skill import SkillService
from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.workflow import Workflow

# ── Thresholds ──────────────────────────────────────────────────────

SYNONYM_LATENCY_MAX_MS = 10.0
MULTILINGUAL_ACCURACY_MIN = 0.70
DISAMBIGUATION_ACCURACY_MIN = 0.65
FUZZY_ACCURACY_MIN = 0.60
COMPOSITE_ACCURACY_MIN = 0.55


def _make_skill(
    skill_id: str,
    display_name: str,
    description: str,
    namespace: str = "default",
) -> SkillEntry:
    """Create a SkillEntry for testing."""
    workflow = Workflow.model_validate({
        "id": skill_id,
        "version": "1",
        "display_name": display_name,
        "description": description,
        "prompt_profile": {"system": "react"},
        "tool_policy": {"max_risk": "low"},
        "steps": [{"type": "prompt", "id": "guide", "prompt": f"Use {display_name}."}],
        "success_criteria": ["Done."],
    })
    return SkillEntry(
        namespace=namespace,
        workflow_id=skill_id,
        display_name=display_name,
        description=description,
        path=Path(f"/tmp/{skill_id}.yaml"),
        workflow=workflow,
        source_kind="skill",
    )


def _create_comprehensive_skills() -> list[SkillEntry]:
    """Create a comprehensive set of skills for testing."""
    return [
        _make_skill(
            "docx",
            "DOCX",
            "Create edit inspect and format Microsoft Word docx documents. "
            "生成 word 文档。支持 document 文件 编辑 创建 写 格式化 "
            "ドキュメント 문서 document",
        ),
        _make_skill(
            "pdf",
            "PDF",
            "Read split merge rotate and extract content from PDF documents. "
            "读取 pdf 文件。支持 提取 合并 拆分 转换 "
            "PDF PDFファイル PDF문서",
        ),
        _make_skill(
            "xlsx",
            "XLSX",
            "Analyze edit calculate formulas and charts in spreadsheets. "
            "分析 excel 表格。支持 spreadsheet 电子表格 公式 计算 "
            "スプレッドシート 스프레드시트",
        ),
        _make_skill(
            "pptx",
            "PPTX",
            "Create edit and inspect PowerPoint presentations and slides. "
            "创建 ppt 演示文稿。支持 presentation 幻灯片 演示 "
            "プレゼンテーション 프레젠테이션",
        ),
        _make_skill(
            "code",
            "Code",
            "Write review debug and refactor source code. "
            "编写 代码 程序 脚本。支持 coding programming script 开发 调试 "
            "コード 코드 프로그램",
        ),
        _make_skill(
            "search",
            "Search",
            "Full text web search with query expansion. "
            "搜索 查找 检索。支持 search lookup find 查询 资料 "
            "検索 검색",
        ),
        _make_skill(
            "email",
            "Email",
            "Send receive and organize email messages. "
            "发送 邮件 电子邮件。支持 mail 信件 消息 "
            "メール 이메일",
        ),
        _make_skill(
            "database",
            "Database",
            "Query manage and optimize database operations. "
            "数据库 查询 管理。支持 db sql 数据 存储 "
            "データベース 데이터베이스",
        ),
        _make_skill(
            "proxy",
            "Proxy",
            "Configure network proxy settings and HTTP proxy servers. "
            "配置 网络代理 HTTP代理 服务器 转发",
        ),
        _make_skill(
            "agent",
            "Agent",
            "Create and manage AI agents for autonomous task execution. "
            "AI代理 智能代理 自动化 任务执行 人工智能",
        ),
        _make_skill(
            "deploy",
            "Deploy",
            "Deploy applications to servers and cloud platforms. "
            "部署 应用 服务器 云平台 发布 容器 Docker",
        ),
        _make_skill(
            "backup",
            "Backup",
            "Create manage and restore data backups and snapshots. "
            "备份 数据 快照 恢复 存储 归档",
        ),
        _make_skill(
            "monitor",
            "Monitor",
            "Monitor system health performance metrics and alerts. "
            "监控 系统 性能 指标 告警 CPU 内存",
        ),
        _make_skill(
            "analyze",
            "Analyze",
            "Analyze data patterns trends and generate insights. "
            "分析 数据 趋势 洞察 报告 可视化",
        ),
        _make_skill(
            "summarize",
            "Summarize",
            "Summarize long documents into key points. "
            "总结 摘要 概括。支持 summary 概述 提炼",
        ),
        _make_skill(
            "translate",
            "Translate",
            "Translate text between multiple languages. "
            "翻译 转换。支持 translate 翻译 语言",
        ),
    ]


# ── Test Cases ──────────────────────────────────────────────────────

SYNONYM_CASES = [
    ("创建一个word文档", "docx"),
    ("编辑doc文件", "docx"),
    ("生成document", "docx"),
    ("打开excel表格", "xlsx"),
    ("修改spreadsheet", "xlsx"),
    ("制作ppt演示文稿", "pptx"),
    ("创建presentation", "pptx"),
    ("编写代码", "code"),
    ("写程序", "code"),
    ("搜索资料", "search"),
    ("发送邮件", "email"),
    ("查询数据库", "database"),
]

MULTILINGUAL_CASES = [
    # Chinese
    ("创建一份文档", "docx"),
    ("编写一段代码", "code"),
    ("搜索相关资料", "search"),
    # English
    ("Create a document", "docx"),
    ("Write some code", "code"),
    ("Search for information", "search"),
    # Mixed
    ("帮我写一个Python脚本", "code"),
    ("Create一份word文档", "docx"),
    # Japanese
    ("ドキュメントを作成する", "docx"),
    ("コードを書く", "code"),
    # Korean
    ("문서를 작성하세요", "docx"),
    ("코드를 작성하세요", "code"),
]

DISAMBIGUATION_CASES = [
    ("配置网络代理服务器", "proxy"),
    ("创建AI代理执行任务", "agent"),
    ("部署应用到服务器", "deploy"),
    ("备份数据库", "backup"),
    ("监控系统性能", "monitor"),
    ("分析数据趋势", "analyze"),
    ("搜索代码中的函数", "code"),
    ("搜索知识库文档", "search"),
    ("执行SQL查询", "database"),
    ("导出CSV格式", "xlsx"),
]

FUZZY_CASES = [
    ("creat a docx", "docx"),
    ("edti document", "docx"),
    ("serach for code", "search"),
    ("write codde", "code"),
    ("那个文档工具", "docx"),
    ("表格", "xlsx"),
    ("演示", "pptx"),
    ("帮我搞个文档", "docx"),
    ("弄个表格", "xlsx"),
    ("写代码", "code"),
    ("doc", "docx"),
    ("ppt", "pptx"),
]

COMPOSITE_CASES = [
    ("搜索并总结", "search"),
    ("读取配置并修改", "code"),
    ("如果有PDF就转换", "pdf"),
    ("先搜索然后总结", "search"),
    ("分析数据并总结", "summarize"),
    ("编写代码并测试", "code"),
]


# ── Performance Tests ───────────────────────────────────────────────


class TestSynonymPerformance:
    """Test synonym matching performance."""

    def test_synonym_lookup_latency(self):
        """Verify synonym lookup latency is within threshold."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        latencies = []
        for query, _ in SYNONYM_CASES * 10:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            latencies.append(time.perf_counter() - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        assert avg_latency_ms < SYNONYM_LATENCY_MAX_MS, (
            f"Average synonym latency {avg_latency_ms:.2f}ms >= {SYNONYM_LATENCY_MAX_MS}ms"
        )

    def test_synonym_accuracy(self):
        """Verify synonym matching accuracy."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        hits = 0
        for query, expected in SYNONYM_CASES:
            service.reset()
            route = service.maybe_auto_select(query)
            if route and route.entry.workflow_id == expected:
                hits += 1

        accuracy = hits / len(SYNONYM_CASES)
        # Record accuracy for analysis
        print(f"\nSynonym accuracy: {accuracy:.2%} ({hits}/{len(SYNONYM_CASES)})")


class TestMultilingualPerformance:
    """Test multilingual routing performance."""

    def test_multilingual_accuracy(self):
        """Verify multilingual routing accuracy."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        hits = 0
        for query, expected in MULTILINGUAL_CASES:
            service.reset()
            route = service.maybe_auto_select(query)
            if route and route.entry.workflow_id == expected:
                hits += 1

        accuracy = hits / len(MULTILINGUAL_CASES)
        assert accuracy >= MULTILINGUAL_ACCURACY_MIN, (
            f"Multilingual accuracy {accuracy:.2%} < {MULTILINGUAL_ACCURACY_MIN:.2%}"
        )

    def test_multilingual_latency(self):
        """Verify multilingual routing latency."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        latencies = []
        for query, _ in MULTILINGUAL_CASES * 10:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            latencies.append(time.perf_counter() - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        assert avg_latency_ms < SYNONYM_LATENCY_MAX_MS, (
            f"Average multilingual latency {avg_latency_ms:.2f}ms >= {SYNONYM_LATENCY_MAX_MS}ms"
        )


class TestDisambiguationPerformance:
    """Test disambiguation performance."""

    def test_disambiguation_accuracy(self):
        """Verify disambiguation accuracy."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        hits = 0
        for query, expected in DISAMBIGUATION_CASES:
            service.reset()
            route = service.maybe_auto_select(query)
            if route and route.entry.workflow_id == expected:
                hits += 1

        accuracy = hits / len(DISAMBIGUATION_CASES)
        assert accuracy >= DISAMBIGUATION_ACCURACY_MIN, (
            f"Disambiguation accuracy {accuracy:.2%} < {DISAMBIGUATION_ACCURACY_MIN:.2%}"
        )

    def test_disambiguation_latency(self):
        """Verify disambiguation routing latency."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        latencies = []
        for query, _ in DISAMBIGUATION_CASES * 10:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            latencies.append(time.perf_counter() - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        assert avg_latency_ms < SYNONYM_LATENCY_MAX_MS, (
            f"Average disambiguation latency {avg_latency_ms:.2f}ms >= {SYNONYM_LATENCY_MAX_MS}ms"
        )


class TestFuzzyPerformance:
    """Test fuzzy matching performance."""

    def test_fuzzy_accuracy(self):
        """Verify fuzzy matching accuracy."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        hits = 0
        for query, expected in FUZZY_CASES:
            service.reset()
            route = service.maybe_auto_select(query)
            if route and route.entry.workflow_id == expected:
                hits += 1

        accuracy = hits / len(FUZZY_CASES)
        assert accuracy >= FUZZY_ACCURACY_MIN, (
            f"Fuzzy accuracy {accuracy:.2%} < {FUZZY_ACCURACY_MIN:.2%}"
        )

    def test_fuzzy_latency(self):
        """Verify fuzzy matching latency."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        latencies = []
        for query, _ in FUZZY_CASES * 10:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            latencies.append(time.perf_counter() - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        assert avg_latency_ms < SYNONYM_LATENCY_MAX_MS, (
            f"Average fuzzy latency {avg_latency_ms:.2f}ms >= {SYNONYM_LATENCY_MAX_MS}ms"
        )


class TestCompositePerformance:
    """Test composite intent performance."""

    def test_composite_accuracy(self):
        """Verify composite intent accuracy."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        hits = 0
        for query, expected in COMPOSITE_CASES:
            service.reset()
            route = service.maybe_auto_select(query)
            if route and route.entry.workflow_id == expected:
                hits += 1

        accuracy = hits / len(COMPOSITE_CASES)
        assert accuracy >= COMPOSITE_ACCURACY_MIN, (
            f"Composite accuracy {accuracy:.2%} < {COMPOSITE_ACCURACY_MIN:.2%}"
        )

    def test_composite_latency(self):
        """Verify composite intent latency."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        latencies = []
        for query, _ in COMPOSITE_CASES * 10:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            latencies.append(time.perf_counter() - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        assert avg_latency_ms < SYNONYM_LATENCY_MAX_MS, (
            f"Average composite latency {avg_latency_ms:.2f}ms >= {SYNONYM_LATENCY_MAX_MS}ms"
        )


class TestOverallPerformance:
    """Test overall complex scenario performance."""

    def test_overall_accuracy_summary(self):
        """Generate overall accuracy summary."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        all_cases = (
            SYNONYM_CASES
            + MULTILINGUAL_CASES
            + DISAMBIGUATION_CASES
            + FUZZY_CASES
            + COMPOSITE_CASES
        )

        hits = 0
        for query, expected in all_cases:
            service.reset()
            route = service.maybe_auto_select(query)
            if route and route.entry.workflow_id == expected:
                hits += 1

        overall_accuracy = hits / len(all_cases)
        print(f"\nOverall accuracy: {overall_accuracy:.2%} ({hits}/{len(all_cases)})")

    def test_overall_latency(self):
        """Verify overall latency under complex scenarios."""
        registry = SkillRegistry([])
        registry._entries = _create_comprehensive_skills()
        service = SkillService(registry)

        all_cases = (
            SYNONYM_CASES
            + MULTILINGUAL_CASES
            + DISAMBIGUATION_CASES
            + FUZZY_CASES
            + COMPOSITE_CASES
        )

        latencies = []
        for query, _ in all_cases * 5:
            service.reset()
            start = time.perf_counter()
            service.maybe_auto_select(query)
            latencies.append(time.perf_counter() - start)

        avg_latency_ms = (sum(latencies) / len(latencies)) * 1000
        p95_latency_ms = sorted(latencies)[int(len(latencies) * 0.95)] * 1000

        print(f"\nAverage latency: {avg_latency_ms:.2f}ms")
        print(f"P95 latency: {p95_latency_ms:.2f}ms")

        assert avg_latency_ms < SYNONYM_LATENCY_MAX_MS, (
            f"Average latency {avg_latency_ms:.2f}ms >= {SYNONYM_LATENCY_MAX_MS}ms"
        )
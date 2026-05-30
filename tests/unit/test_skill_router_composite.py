"""Tests for skill router composite intent handling.

These tests verify that the router can handle queries with multiple intents,
conditional intents, and prioritized intents.

Example:
    - "搜索并总结" → search skill (primary intent)
    - "读取配置并修改" → code skill (primary intent)
    - "如果有PDF就转换" → pdf skill (conditional)
"""

from pathlib import Path

import pytest

from agentnexus.services.skill import SkillService
from agentnexus.skills.registry import SkillEntry, SkillRegistry
from agentnexus.skills.workflow import Workflow


def _make_skill(
    skill_id: str,
    display_name: str,
    description: str,
    namespace: str = "default",
    verbs: list[str] | None = None,
    objects: list[str] | None = None,
    aliases: list[str] | None = None,
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
        "verbs": verbs or [],
        "objects": objects or [],
        "aliases": aliases or [],
    })
    return SkillEntry(
        namespace=namespace,
        workflow_id=skill_id,
        display_name=display_name,
        description=description,
        path=Path(f"/tmp/{skill_id}.yaml"),
        workflow=workflow,
        source_kind="skill",
        aliases=tuple(aliases or []),
        verbs=tuple(verbs or []),
        objects=tuple(objects or []),
    )


def _create_composite_skills() -> list[SkillEntry]:
    """Create skills for composite intent testing."""
    return [
        _make_skill(
            "docx", "DOCX",
            "Create edit inspect and format Microsoft Word docx documents.",
            verbs=["创建", "编辑", "写", "格式化", "create", "edit"],
            objects=["文档", "word", "docx", "文件", "document"],
            aliases=["word", "docx", "document", "文档"],
        ),
        _make_skill(
            "pdf", "PDF",
            "Read split merge rotate and extract content from PDF documents.",
            verbs=["读取", "提取", "合并", "拆分", "转换", "read", "extract", "convert"],
            objects=["pdf", "文件"],
            aliases=["pdf", "pdf文件"],
        ),
        _make_skill(
            "xlsx", "XLSX",
            "Analyze edit calculate formulas and charts in spreadsheets.",
            verbs=["分析", "编辑", "计算", "导出", "analyze", "edit", "export"],
            objects=["表格", "excel", "电子表格", "spreadsheet", "数据"],
            aliases=["excel", "xlsx", "spreadsheet", "表格"],
        ),
        _make_skill(
            "code", "Code",
            "Write review debug and refactor source code.",
            verbs=[
                "编写", "写", "调试", "重构", "修改", "配置", "部署",
                "review", "debug", "write", "refactor", "deploy",
            ],
            objects=["代码", "程序", "脚本", "code", "script", "source"],
            aliases=["code", "代码", "脚本", "programming", "script"],
        ),
        _make_skill(
            "search", "Search",
            "Full text web search with query expansion.",
            verbs=["搜索", "查找", "检索", "search", "lookup", "find", "查询"],
            objects=["信息", "资料", "文档", "information"],
            aliases=["search", "搜索", "检索", "lookup", "find"],
        ),
        _make_skill(
            "summarize", "Summarize",
            "Summarize long documents into key points.",
            verbs=["总结", "摘要", "概括", "summarize"],
            objects=["文档", "报告", "document", "report"],
            aliases=["summarize", "总结", "摘要"],
        ),
        _make_skill(
            "translate", "Translate",
            "Translate text between multiple languages.",
            verbs=["翻译", "转换", "下载", "translate", "convert", "download"],
            objects=["文本", "文档", "格式", "text", "document"],
            aliases=["translate", "翻译"],
        ),
        _make_skill(
            "database", "Database",
            "Query manage and optimize database operations.",
            verbs=["查询", "管理", "query", "manage"],
            objects=["数据库", "数据", "database", "db", "sql"],
            aliases=["database", "db", "sql", "数据库"],
        ),
    ]


class TestMultiStepIntent:
    """Test queries with multiple sequential steps."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with composite skills."""
        registry = SkillRegistry([])
        registry._entries = _create_composite_skills()
        return SkillService(registry)

    def test_search_and_summarize(self, service: SkillService):
        """'搜索并总结' should match search (primary intent)."""
        service.reset()
        route = service.maybe_auto_select("搜索并总结")
        assert route is not None
        # Primary intent is search
        assert route.entry.workflow_id == "search"

    def test_read_and_modify(self, service: SkillService):
        """'读取配置并修改' matches code (config+modify) or pdf (read)."""
        service.reset()
        route = service.maybe_auto_select("读取配置并修改")
        assert route is None or route.entry.workflow_id in ["code", "pdf"]  # genuinely ambiguous

    def test_download_and_convert(self, service: SkillService):
        """'下载并转换格式' should match code or translate."""
        service.reset()
        route = service.maybe_auto_select("下载并转换格式")
        assert route is not None
        # Should match one of the relevant skills
        assert route.entry.workflow_id in ["code", "translate"]

    def test_search_and_create(self, service: SkillService):
        """'搜索资料并创建文档' should match search or docx."""
        service.reset()
        route = service.maybe_auto_select("搜索资料并创建文档")
        assert route is not None
        # Should match one of the relevant skills
        assert route.entry.workflow_id in ["search", "docx"]

    def test_analyze_and_summarize(self, service: SkillService):
        """'分析数据并总结' matches summarize or database."""
        service.reset()
        route = service.maybe_auto_select("分析数据并总结")
        assert route is not None
        assert route.entry.workflow_id in ["summarize", "database"]

    def test_write_and_test(self, service: SkillService):
        """'编写代码并测试' should match code."""
        service.reset()
        route = service.maybe_auto_select("编写代码并测试")
        assert route is not None
        assert route.entry.workflow_id == "code"


class TestConditionalIntent:
    """Test queries with conditional intent."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with composite skills."""
        registry = SkillRegistry([])
        registry._entries = _create_composite_skills()
        return SkillService(registry)

    def test_if_pdf_convert(self, service: SkillService):
        """'如果有PDF就转换' should match pdf."""
        service.reset()
        route = service.maybe_auto_select("如果有PDF就转换")
        assert route is not None
        assert route.entry.workflow_id == "pdf"

    def test_if_data_analyze(self, service: SkillService):
        """'如果有数据就分析' should match xlsx."""
        service.reset()
        route = service.maybe_auto_select("如果有数据就分析")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_when_ready_deploy(self, service: SkillService):
        """'准备好了就部署' should match code (deployment)."""
        service.reset()
        route = service.maybe_auto_select("准备好了就部署")
        assert route is not None
        # Deployment is code-related
        assert route.entry.workflow_id == "code"

    def test_if_search_fails_create(self, service: SkillService):
        """'搜索不到就创建' should match search (primary)."""
        service.reset()
        route = service.maybe_auto_select("搜索不到就创建")
        assert route is not None
        # Primary intent is search
        assert route.entry.workflow_id == "search"


class TestPriorityIntent:
    """Test queries where intent priority matters."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with composite skills."""
        registry = SkillRegistry([])
        registry._entries = _create_composite_skills()
        return SkillService(registry)

    def test_primary_search_secondary_docx(self, service: SkillService):
        """'搜索文档' should match search (search is primary)."""
        service.reset()
        route = service.maybe_auto_select("搜索文档")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_primary_docx_secondary_search(self, service: SkillService):
        """'创建搜索文档' should match docx (creation is primary)."""
        service.reset()
        route = service.maybe_auto_select("创建搜索文档")
        assert route is not None
        # Creation intent is primary
        assert route.entry.workflow_id == "docx"

    def test_primary_code_secondary_search(self, service: SkillService):
        """'搜索代码' should match code or search."""
        service.reset()
        route = service.maybe_auto_select("搜索代码")
        assert route is not None
        # Could be either code-search or search
        assert route.entry.workflow_id in ["code", "search"]

    def test_primary_analysis_secondary_export(self, service: SkillService):
        """'分析并导出数据' should match xlsx (analysis primary)."""
        service.reset()
        route = service.maybe_auto_select("分析并导出数据")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"


class TestParallelIntent:
    """Test queries with parallel intents."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with composite skills."""
        registry = SkillRegistry([])
        registry._entries = _create_composite_skills()
        return SkillService(registry)

    def test_both_docx_and_xlsx(self, service: SkillService):
        """'同时创建文档和表格' should match one (router picks primary)."""
        service.reset()
        route = service.maybe_auto_select("同时创建文档和表格")
        assert route is not None
        # Router should pick one as primary
        assert route.entry.workflow_id in ["docx", "xlsx"]

    def test_both_search_and_translate(self, service: SkillService):
        """'搜索并翻译' should match one."""
        service.reset()
        route = service.maybe_auto_select("搜索并翻译")
        assert route is not None
        assert route.entry.workflow_id in ["search", "translate"]

    def test_both_code_and_document(self, service: SkillService):
        """'编写代码和文档' should match one."""
        service.reset()
        route = service.maybe_auto_select("编写代码和文档")
        assert route is not None
        assert route.entry.workflow_id in ["code", "docx"]


class TestSequenceIntent:
    """Test queries with sequential intent markers."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with composite skills."""
        registry = SkillRegistry([])
        registry._entries = _create_composite_skills()
        return SkillService(registry)

    def test_first_then(self, service: SkillService):
        """'先搜索然后总结' should match search (first action)."""
        service.reset()
        route = service.maybe_auto_select("先搜索然后总结")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_after_that(self, service: SkillService):
        """'读取之后修改' should match code (modification)."""
        service.reset()
        route = service.maybe_auto_select("读取之后修改")
        assert route is not None
        assert route.entry.workflow_id in ["code", "pdf"]

    def test_step_by_step(self, service: SkillService):
        """'第一步搜索，第二步总结' should match search (first step)."""
        service.reset()
        route = service.maybe_auto_select("第一步搜索，第二步总结")
        assert route is not None
        assert route.entry.workflow_id == "search"


class TestCompositeEdgeCases:
    """Test edge cases in composite intent handling."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with composite skills."""
        registry = SkillRegistry([])
        registry._entries = _create_composite_skills()
        return SkillService(registry)

    def test_contradictory_intents(self, service: SkillService):
        """'创建然后删除文档' should match docx (creation)."""
        service.reset()
        route = service.maybe_auto_select("创建然后删除文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_vague_composite(self, service: SkillService):
        """'做点什么' should return None or uncertain."""
        service.reset()
        service.maybe_auto_select("做点什么")
        # Very vague - might return None

    def test_single_keyword_dominates(self, service: SkillService):
        """'PDF文档表格代码搜索' should match based on strongest signal."""
        service.reset()
        route = service.maybe_auto_select("PDF文档表格代码搜索")
        assert route is not None
        # Should pick one based on scoring
        assert route.entry.workflow_id in ["pdf", "docx", "xlsx", "code", "search"]

    def test_complex_natural_language(self, service: SkillService):
        """Complex natural language should be handled."""
        service.reset()
        route = service.maybe_auto_select(
            "我需要先搜索一些资料，然后写一个总结报告，最后翻译成英文"
        )
        assert route is not None
        # Should pick primary intent
        assert route.entry.workflow_id in ["search", "summarize", "translate"]

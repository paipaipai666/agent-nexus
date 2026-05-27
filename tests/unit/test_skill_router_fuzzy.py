"""Tests for skill router fuzzy matching capabilities.

These tests verify that the router can handle:
- Typos and spelling errors
- Incomplete queries
- Colloquial expressions
- Abbreviations

Example:
    - "creat a docx" → docx skill (typo)
    - "那个文档工具" → docx skill (incomplete)
    - "帮我搞个文档" → docx skill (colloquial)
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


def _create_fuzzy_skills() -> list[SkillEntry]:
    """Create skills for fuzzy matching tests."""
    return [
        _make_skill(
            "docx", "DOCX",
            "Create edit inspect and format Microsoft Word docx documents.",
            verbs=["创建", "编辑", "写", "create", "edit"],
            objects=["文档", "word", "docx", "文件", "document"],
            aliases=["word", "docx", "document", "文档"],
        ),
        _make_skill(
            "pdf", "PDF",
            "Read split merge rotate and extract content from PDF documents.",
            verbs=["读取", "提取", "合并", "read", "extract"],
            objects=["pdf", "文件"],
            aliases=["pdf", "pdf文件"],
        ),
        _make_skill(
            "xlsx", "XLSX",
            "Analyze edit calculate formulas and charts in spreadsheets.",
            verbs=["分析", "编辑", "计算", "analyze", "edit"],
            objects=["表格", "excel", "电子表格", "spreadsheet"],
            aliases=["excel", "xlsx", "spreadsheet", "表格"],
        ),
        _make_skill(
            "pptx", "PPTX",
            "Create edit and inspect PowerPoint presentations and slides.",
            verbs=["创建", "编辑", "制作", "create", "edit"],
            objects=["ppt", "演示文稿", "幻灯片", "presentation", "slides"],
            aliases=["powerpoint", "pptx", "slides", "演示"],
        ),
        _make_skill(
            "code", "Code",
            "Write review debug and refactor source code.",
            verbs=["编写", "写", "调试", "重构", "review", "debug", "write"],
            objects=["代码", "程序", "脚本", "code", "script", "source"],
            aliases=["code", "代码", "脚本", "programming", "script"],
        ),
        _make_skill(
            "search", "Search",
            "Full text web search with query expansion.",
            verbs=["搜索", "查找", "检索", "search", "lookup", "find"],
            objects=["信息", "资料", "文档", "information"],
            aliases=["search", "搜索", "检索", "lookup", "find"],
        ),
    ]


class TestTypoTolerance:
    """Test tolerance for common typos."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with fuzzy skills."""
        registry = SkillRegistry([])
        registry._entries = _create_fuzzy_skills()
        return SkillService(registry)

    def test_creat_typo(self, service: SkillService):
        """'creat' (missing 'e') should still match docx."""
        service.reset()
        route = service.maybe_auto_select("creat a docx")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_edti_typo(self, service: SkillService):
        """'edti' (transposed) should still match docx."""
        service.reset()
        route = service.maybe_auto_select("edti document")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_serach_typo(self, service: SkillService):
        """'serach' (transposed) should still match search."""
        service.reset()
        route = service.maybe_auto_select("serach for information")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_codde_typo(self, service: SkillService):
        """'codde' (double 'd') should still match code."""
        service.reset()
        route = service.maybe_auto_select("write codde")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_spreadshet_typo(self, service: SkillService):
        """'spreadshet' (missing 'e') should still match xlsx."""
        service.reset()
        route = service.maybe_auto_select("create spreadshet")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_presentaton_typo(self, service: SkillService):
        """'presentaton' (missing 'i') should still match pptx."""
        service.reset()
        route = service.maybe_auto_select("make presentaton")
        assert route is not None
        assert route.entry.workflow_id == "pptx"


class TestIncompleteQueries:
    """Test handling of incomplete or vague queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with fuzzy skills."""
        registry = SkillRegistry([])
        registry._entries = _create_fuzzy_skills()
        return SkillService(registry)

    def test_vague_document_tool(self, service: SkillService):
        """'那个文档工具' (that document tool) should match docx."""
        service.reset()
        route = service.maybe_auto_select("那个文档工具")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_vague_table_tool(self, service: SkillService):
        """'表格' (table/spreadsheet) should match xlsx."""
        service.reset()
        route = service.maybe_auto_select("表格")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_vague_slides_tool(self, service: SkillService):
        """'演示' (presentation) should match pptx."""
        service.reset()
        route = service.maybe_auto_select("演示")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_vague_code_tool(self, service: SkillService):
        """'代码' (code) should match code."""
        service.reset()
        route = service.maybe_auto_select("代码")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_vague_search_tool(self, service: SkillService):
        """'搜索' (search) should match search."""
        service.reset()
        route = service.maybe_auto_select("搜索")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_single_word_doc(self, service: SkillService):
        """Single word 'doc' should match docx."""
        service.reset()
        route = service.maybe_auto_select("doc")
        assert route is not None
        assert route.entry.workflow_id == "docx"


class TestColloquialExpressions:
    """Test handling of colloquial/口语化 expressions."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with fuzzy skills."""
        registry = SkillRegistry([])
        registry._entries = _create_fuzzy_skills()
        return SkillService(registry)

    def test_gao_ge_wendang(self, service: SkillService):
        """'帮我搞个文档' (help me get a document) should match docx."""
        service.reset()
        route = service.maybe_auto_select("帮我搞个文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_nong_ge_biaoge(self, service: SkillService):
        """'弄个表格' (make a spreadsheet) should match xlsx."""
        service.reset()
        route = service.maybe_auto_select("弄个表格")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_gao_yixia_ppt(self, service: SkillService):
        """'搞一下PPT' (do a PPT) should match pptx."""
        service.reset()
        route = service.maybe_auto_select("搞一下PPT")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_xie_daima(self, service: SkillService):
        """'写代码' (write code) should match code."""
        service.reset()
        route = service.maybe_auto_select("写代码")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_sousuo_yixia(self, service: SkillService):
        """'搜索一下' (search a bit) should match search."""
        service.reset()
        route = service.maybe_auto_select("搜索一下")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_gei_wo_zhao(self, service: SkillService):
        """'给我找找' (find for me) should match search."""
        service.reset()
        route = service.maybe_auto_select("给我找找")
        assert route is not None
        assert route.entry.workflow_id == "search"


class TestAbbreviations:
    """Test handling of abbreviations."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with fuzzy skills."""
        registry = SkillRegistry([])
        registry._entries = _create_fuzzy_skills()
        return SkillService(registry)

    def test_doc_abbreviation(self, service: SkillService):
        """'doc' abbreviation should match docx."""
        service.reset()
        route = service.maybe_auto_select("create doc")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_ppt_abbreviation(self, service: SkillService):
        """'ppt' abbreviation should match pptx."""
        service.reset()
        route = service.maybe_auto_select("make ppt")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_xls_abbreviation(self, service: SkillService):
        """'xls' abbreviation should match xlsx."""
        service.reset()
        route = service.maybe_auto_select("open xls")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_py_abbreviation(self, service: SkillService):
        """'py' for Python should match code."""
        service.reset()
        route = service.maybe_auto_select("write py script")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_js_abbreviation(self, service: SkillService):
        """'js' for JavaScript should match code."""
        service.reset()
        route = service.maybe_auto_select("write js code")
        assert route is not None
        assert route.entry.workflow_id == "code"


class TestNaturalLanguageVariations:
    """Test various natural language formulations."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with fuzzy skills."""
        registry = SkillRegistry([])
        registry._entries = _create_fuzzy_skills()
        return SkillService(registry)

    def test_question_form(self, service: SkillService):
        """Question form should work."""
        service.reset()
        route = service.maybe_auto_select("如何创建文档？")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_request_form(self, service: SkillService):
        """Request form should work."""
        service.reset()
        route = service.maybe_auto_select("请帮我创建文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_command_form(self, service: SkillService):
        """Command form should work."""
        service.reset()
        route = service.maybe_auto_select("创建文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_indirect_form(self, service: SkillService):
        """Indirect form should work."""
        service.reset()
        route = service.maybe_auto_select("我需要一个文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_capability_form(self, service: SkillService):
        """Capability question should work."""
        service.reset()
        route = service.maybe_auto_select("你能创建文档吗？")
        assert route is not None
        assert route.entry.workflow_id == "docx"


class TestEdgeCases:
    """Test edge cases in fuzzy matching."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with fuzzy skills."""
        registry = SkillRegistry([])
        registry._entries = _create_fuzzy_skills()
        return SkillService(registry)

    def test_empty_query(self, service: SkillService):
        """Empty query should return None."""
        service.reset()
        route = service.maybe_auto_select("")
        assert route is None

    def test_whitespace_only(self, service: SkillService):
        """Whitespace-only query should return None."""
        service.reset()
        route = service.maybe_auto_select("   ")
        assert route is None

    def test_stopwords_only(self, service: SkillService):
        """Stopwords-only query should return None."""
        service.reset()
        route = service.maybe_auto_select("的 了 是")
        assert route is None

    def test_numbers_only(self, service: SkillService):
        """Numbers-only query should return None."""
        service.reset()
        route = service.maybe_auto_select("12345")
        assert route is None

    def test_special_chars_only(self, service: SkillService):
        """Special chars-only query should return None."""
        service.reset()
        route = service.maybe_auto_select("!@#$%")
        assert route is None

    def test_very_long_query(self, service: SkillService):
        """Very long query should still work."""
        long_query = "我想创建一个文档 " * 100 + "word"
        service.reset()
        route = service.maybe_auto_select(long_query)
        assert route is not None
        assert route.entry.workflow_id == "docx"
"""Tests for skill router synonym matching capabilities.

These tests verify that the router can match queries containing synonyms,
abbreviations, and alternative terms to the correct skills.

Example:
    - "word" → "docx"
    - "excel" → "xlsx"
    - "ppt" → "pptx"
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


def _create_office_skills() -> list[SkillEntry]:
    """Create Office document skills with rich metadata."""
    return [
        _make_skill(
            "docx",
            "DOCX",
            "Create edit inspect and format Microsoft Word docx documents. "
            "生成 word 文档。支持 document document 文档 文件 编辑 格式化",
        ),
        _make_skill(
            "pdf",
            "PDF",
            "Read split merge rotate and extract content from PDF documents. "
            "读取 pdf 文件。支持 提取 合并 拆分 旋转 阅读",
        ),
        _make_skill(
            "xlsx",
            "XLSX",
            "Analyze edit calculate formulas and charts in spreadsheets. "
            "分析 excel 表格。支持 spreadsheet 电子表格 公式 图表 计算",
        ),
        _make_skill(
            "pptx",
            "PPTX",
            "Create edit and inspect PowerPoint presentations and slides. "
            "创建 ppt 演示文稿。支持 presentation 幻灯片 演示 slides",
        ),
    ]


def _create_tech_skills() -> list[SkillEntry]:
    """Create technical skills with rich metadata."""
    return [
        _make_skill(
            "code",
            "Code",
            "Write review debug and refactor source code. "
            "编写 代码 程序 脚本。支持 coding programming script 开发 调试 重构",
        ),
        _make_skill(
            "search",
            "Search",
            "Full text web search with query expansion. "
            "搜索 查找 检索。支持 search lookup find 查询 资料 文档",
        ),
        _make_skill(
            "email",
            "Email",
            "Send receive and organize email messages. "
            "发送 邮件 电子邮件。支持 mail 信件 消息 收件",
        ),
        _make_skill(
            "database",
            "Database",
            "Query manage and optimize database operations. "
            "数据库 查询 管理。支持 db sql 数据 存储 表",
        ),
    ]


class TestOfficeDocumentSynonyms:
    """Test synonym matching for Office document skills."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with Office skills."""
        registry = SkillRegistry([])
        registry._entries = _create_office_skills()
        return SkillService(registry)

    def test_word_to_docx(self, service: SkillService):
        """'word' should match docx skill."""
        service.reset()
        route = service.maybe_auto_select("创建一个word文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_doc_to_docx(self, service: SkillService):
        """'doc' should match docx skill."""
        service.reset()
        route = service.maybe_auto_select("编辑doc文件")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_document_to_docx(self, service: SkillService):
        """'document' should match docx skill."""
        service.reset()
        route = service.maybe_auto_select("生成document")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_wendang_to_docx(self, service: SkillService):
        """'文档' (Chinese for document) should match docx skill."""
        service.reset()
        route = service.maybe_auto_select("创建文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_excel_to_xlsx(self, service: SkillService):
        """'excel' should match xlsx skill."""
        service.reset()
        route = service.maybe_auto_select("打开excel表格")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_spreadsheet_to_xlsx(self, service: SkillService):
        """'spreadsheet' should match xlsx skill."""
        service.reset()
        route = service.maybe_auto_select("修改spreadsheet")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_biaoge_to_xlsx(self, service: SkillService):
        """'表格' (Chinese for table/spreadsheet) should match xlsx skill."""
        service.reset()
        route = service.maybe_auto_select("创建表格")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_ppt_to_pptx(self, service: SkillService):
        """'ppt' should match pptx skill."""
        service.reset()
        route = service.maybe_auto_select("制作ppt演示文稿")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_presentation_to_pptx(self, service: SkillService):
        """'presentation' should match pptx skill."""
        service.reset()
        route = service.maybe_auto_select("创建presentation")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_slides_to_pptx(self, service: SkillService):
        """'slides' should match pptx skill."""
        service.reset()
        route = service.maybe_auto_select("编辑slides")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_yanshi_to_pptx(self, service: SkillService):
        """'演示' (Chinese for presentation) should match pptx skill."""
        service.reset()
        route = service.maybe_auto_select("制作演示文稿")
        assert route is not None
        assert route.entry.workflow_id == "pptx"

    def test_pdf_to_pdf(self, service: SkillService):
        """'pdf' should match pdf skill directly."""
        service.reset()
        route = service.maybe_auto_select("读取pdf文件")
        assert route is not None
        assert route.entry.workflow_id == "pdf"


class TestTechnicalTermSynonyms:
    """Test synonym matching for technical skills."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with technical skills."""
        registry = SkillRegistry([])
        registry._entries = _create_tech_skills()
        return SkillService(registry)

    def test_daima_to_code(self, service: SkillService):
        """'代码' (Chinese for code) should match code skill."""
        service.reset()
        route = service.maybe_auto_select("编写代码")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_chengxu_to_code(self, service: SkillService):
        """'程序' (Chinese for program) should match code skill."""
        service.reset()
        route = service.maybe_auto_select("写一个程序")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_jiaoben_to_code(self, service: SkillService):
        """'脚本' (Chinese for script) should match code skill."""
        service.reset()
        route = service.maybe_auto_select("创建脚本")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_programming_to_code(self, service: SkillService):
        """'programming' should match code skill."""
        service.reset()
        route = service.maybe_auto_select("programming task")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_script_to_code(self, service: SkillService):
        """'script' should match code skill."""
        service.reset()
        route = service.maybe_auto_select("write a script")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_sousuo_to_search(self, service: SkillService):
        """'搜索' (Chinese for search) should match search skill."""
        service.reset()
        route = service.maybe_auto_select("搜索资料")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_chazhao_to_search(self, service: SkillService):
        """'查找' (Chinese for find) should match search skill."""
        service.reset()
        route = service.maybe_auto_select("查找文档")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_jiansuo_to_search(self, service: SkillService):
        """'检索' (Chinese for retrieve) should match search skill."""
        service.reset()
        route = service.maybe_auto_select("检索信息")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_lookup_to_search(self, service: SkillService):
        """'lookup' should match search skill."""
        service.reset()
        route = service.maybe_auto_select("lookup information")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_find_to_search(self, service: SkillService):
        """'find' should match search skill."""
        service.reset()
        route = service.maybe_auto_select("find documents")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_youjian_to_email(self, service: SkillService):
        """'邮件' (Chinese for email) should match email skill."""
        service.reset()
        route = service.maybe_auto_select("发送邮件")
        assert route is not None
        assert route.entry.workflow_id == "email"

    def test_xinjian_to_email(self, service: SkillService):
        """'信件' (Chinese for letter) should match email skill."""
        service.reset()
        route = service.maybe_auto_select("写信件")
        assert route is not None
        assert route.entry.workflow_id == "email"

    def test_shuju_to_database(self, service: SkillService):
        """'数据' (Chinese for data) should match database skill."""
        service.reset()
        route = service.maybe_auto_select("查询数据")
        assert route is not None
        assert route.entry.workflow_id == "database"

    def test_sql_to_database(self, service: SkillService):
        """'sql' should match database skill."""
        service.reset()
        route = service.maybe_auto_select("执行sql查询")
        assert route is not None
        assert route.entry.workflow_id == "database"


class TestCrossCategorySynonyms:
    """Test that synonyms don't cause cross-category confusion."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with mixed skills."""
        registry = SkillRegistry([])
        registry._entries = _create_office_skills() + _create_tech_skills()
        return SkillService(registry)

    def test_code_not_confused_with_document(self, service: SkillService):
        """'代码文档' should match code (primary intent)."""
        service.reset()
        route = service.maybe_auto_select("编写代码文档")
        assert route is not None
        # Should match code, not docx
        assert route.entry.workflow_id == "code"

    def test_search_not_confused_with_database(self, service: SkillService):
        """'搜索数据库' should match database (primary intent)."""
        service.reset()
        route = service.maybe_auto_select("搜索数据库")
        assert route is not None
        # Should match database, not search
        assert route.entry.workflow_id == "database"

    def test_email_document_distinct(self, service: SkillService):
        """'邮件文档' should match email (primary intent)."""
        service.reset()
        route = service.maybe_auto_select("发送邮件文档")
        assert route is not None
        assert route.entry.workflow_id == "email"
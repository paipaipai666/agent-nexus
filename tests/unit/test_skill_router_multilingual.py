"""Tests for skill router multilingual capabilities.

These tests verify that the router can handle queries in multiple languages
and mixed-language inputs.

Example:
    - "帮我写一个Python脚本" → code skill (Chinese)
    - "Create一份word文档" → docx skill (Mixed)
    - "ドキュメント作成" → docx skill (Japanese)
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


def _create_multilingual_skills() -> list[SkillEntry]:
    """Create skills with multilingual metadata."""
    return [
        _make_skill(
            "docx",
            "DOCX",
            "Create edit inspect and format Microsoft Word docx documents. "
            "生成 word 文档。文档 ドキュメント 문서 文件 document",
        ),
        _make_skill(
            "pdf",
            "PDF",
            "Read split merge rotate and extract content from PDF documents. "
            "读取 pdf 文件。PDF PDFファイル PDF문서",
        ),
        _make_skill(
            "xlsx",
            "XLSX",
            "Analyze edit calculate formulas and charts in spreadsheets. "
            "分析 excel 表格。スプレッドシート 스프레드시트 spreadsheet",
        ),
        _make_skill(
            "pptx",
            "PPTX",
            "Create edit and inspect PowerPoint presentations and slides. "
            "创建 ppt 演示文稿。プレゼンテーション 프레젠테이션 presentation",
        ),
        _make_skill(
            "code",
            "Code",
            "Write review debug and refactor source code. "
            "编写 代码 程序 脚本。コード コード 프로그램 code script",
        ),
        _make_skill(
            "search",
            "Search",
            "Full text web search with query expansion. "
            "搜索 查找 检索。検索 검색 search find lookup",
        ),
        _make_skill(
            "email",
            "Email",
            "Send receive and organize email messages. "
            "发送 邮件 电子邮件。メール 이메일 email mail",
        ),
        _make_skill(
            "database",
            "Database",
            "Query manage and optimize database operations. "
            "数据库 查询 管理。データベース 데이터베이스 database db",
        ),
    ]


class TestChineseQueries:
    """Test routing with pure Chinese queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with multilingual skills."""
        registry = SkillRegistry([])
        registry._entries = _create_multilingual_skills()
        return SkillService(registry)

    def test_chinese_docx(self, service: SkillService):
        """Chinese query for document creation."""
        service.reset()
        route = service.maybe_auto_select("创建一份文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_chinese_code(self, service: SkillService):
        """Chinese query for code writing."""
        service.reset()
        route = service.maybe_auto_select("编写一段代码")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_chinese_search(self, service: SkillService):
        """Chinese query for search."""
        service.reset()
        route = service.maybe_auto_select("搜索相关资料")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_chinese_email(self, service: SkillService):
        """Chinese query for email."""
        service.reset()
        route = service.maybe_auto_select("发送一封邮件")
        assert route is not None
        assert route.entry.workflow_id == "email"

    def test_chinese_database(self, service: SkillService):
        """Chinese query for database."""
        service.reset()
        route = service.maybe_auto_select("查询数据库")
        assert route is not None
        assert route.entry.workflow_id == "database"

    def test_chinese_xlsx(self, service: SkillService):
        """Chinese query for spreadsheet."""
        service.reset()
        route = service.maybe_auto_select("创建一个表格")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_chinese_pptx(self, service: SkillService):
        """Chinese query for presentation."""
        service.reset()
        route = service.maybe_auto_select("制作演示文稿")
        assert route is not None
        assert route.entry.workflow_id == "pptx"


class TestEnglishQueries:
    """Test routing with pure English queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with multilingual skills."""
        registry = SkillRegistry([])
        registry._entries = _create_multilingual_skills()
        return SkillService(registry)

    def test_english_docx(self, service: SkillService):
        """English query for document creation."""
        service.reset()
        route = service.maybe_auto_select("Create a document")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_english_code(self, service: SkillService):
        """English query for code writing."""
        service.reset()
        route = service.maybe_auto_select("Write some code")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_english_search(self, service: SkillService):
        """English query for search."""
        service.reset()
        route = service.maybe_auto_select("Search for information")
        assert route is not None
        assert route.entry.workflow_id == "search"

    def test_english_email(self, service: SkillService):
        """English query for email."""
        service.reset()
        route = service.maybe_auto_select("Send an email")
        assert route is not None
        assert route.entry.workflow_id == "email"

    def test_english_database(self, service: SkillService):
        """English query for database."""
        service.reset()
        route = service.maybe_auto_select("Query the database")
        assert route is not None
        assert route.entry.workflow_id == "database"

    def test_english_xlsx(self, service: SkillService):
        """English query for spreadsheet."""
        service.reset()
        route = service.maybe_auto_select("Create a spreadsheet")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_english_pptx(self, service: SkillService):
        """English query for presentation."""
        service.reset()
        route = service.maybe_auto_select("Create a presentation")
        assert route is not None
        assert route.entry.workflow_id == "pptx"


class TestMixedLanguageQueries:
    """Test routing with mixed Chinese-English queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with multilingual skills."""
        registry = SkillRegistry([])
        registry._entries = _create_multilingual_skills()
        return SkillService(registry)

    def test_mixed_docx_english_chinese(self, service: SkillService):
        """Mixed query: English then Chinese."""
        service.reset()
        route = service.maybe_auto_select("Create一份word文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_mixed_docx_chinese_english(self, service: SkillService):
        """Mixed query: Chinese then English."""
        service.reset()
        route = service.maybe_auto_select("帮我写一个document")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_mixed_code_python(self, service: SkillService):
        """Mixed query with Python."""
        service.reset()
        route = service.maybe_auto_select("帮我写一个Python脚本")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_mixed_xlsx_excel(self, service: SkillService):
        """Mixed query with Excel."""
        service.reset()
        route = service.maybe_auto_select("用Excel分析数据")
        assert route is not None
        assert route.entry.workflow_id == "xlsx"

    def test_mixed_pdf_convert(self, service: SkillService):
        """Mixed query for PDF conversion."""
        service.reset()
        route = service.maybe_auto_select("把这份PDF转换成word")
        assert route is not None
        # Should match either pdf or docx
        assert route.entry.workflow_id in ["pdf", "docx"]

    def test_mixed_search_english(self, service: SkillService):
        """Mixed query with English keyword."""
        service.reset()
        route = service.maybe_auto_select("搜索Python documentation")
        assert route is not None
        assert route.entry.workflow_id == "search"


class TestJapaneseQueries:
    """Test routing with Japanese queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with multilingual skills."""
        registry = SkillRegistry([])
        registry._entries = _create_multilingual_skills()
        return SkillService(registry)

    def test_japanese_docx(self, service: SkillService):
        """Japanese query for document creation."""
        service.reset()
        route = service.maybe_auto_select("ドキュメントを作成する")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_japanese_code(self, service: SkillService):
        """Japanese query for code."""
        service.reset()
        route = service.maybe_auto_select("コードを書く")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_japanese_search(self, service: SkillService):
        """Japanese query for search."""
        service.reset()
        route = service.maybe_auto_select("情報を検索する")
        assert route is not None
        assert route.entry.workflow_id == "search"


class TestKoreanQueries:
    """Test routing with Korean queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with multilingual skills."""
        registry = SkillRegistry([])
        registry._entries = _create_multilingual_skills()
        return SkillService(registry)

    def test_korean_docx(self, service: SkillService):
        """Korean query for document creation."""
        service.reset()
        route = service.maybe_auto_select("문서를 작성하세요")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_korean_code(self, service: SkillService):
        """Korean query for code."""
        service.reset()
        route = service.maybe_auto_select("코드를 작성하세요")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_korean_search(self, service: SkillService):
        """Korean query for search."""
        service.reset()
        route = service.maybe_auto_select("정보를 검색하세요")
        assert route is not None
        assert route.entry.workflow_id == "search"


class TestUnicodeHandling:
    """Test proper Unicode handling in queries."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with multilingual skills."""
        registry = SkillRegistry([])
        registry._entries = _create_multilingual_skills()
        return SkillService(registry)

    def test_fullwidth_characters(self, service: SkillService):
        """Full-width characters should be handled."""
        service.reset()
        route = service.maybe_auto_select("创建一份ｗｏｒｄ文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_mixed_scripts(self, service: SkillService):
        """Multiple scripts in one query."""
        service.reset()
        route = service.maybe_auto_select("Pythonのコードを書く")
        assert route is not None
        assert route.entry.workflow_id == "code"

    def test_emoji_in_query(self, service: SkillService):
        """Query with emoji should still work."""
        service.reset()
        route = service.maybe_auto_select("📝 创建文档")
        assert route is not None
        assert route.entry.workflow_id == "docx"

    def test_special_characters(self, service: SkillService):
        """Query with special characters."""
        service.reset()
        route = service.maybe_auto_select("创建文档.docx")
        assert route is not None
        assert route.entry.workflow_id == "docx"
"""Tests for skill router context-based disambiguation.

These tests verify that the router can disambiguate queries where the same
keyword has different meanings in different contexts.

Example:
    - "代理" → agent (AI) vs proxy (network)
    - "部署" → deploy (app) vs backup (data)
    - "监控" → monitor (system) vs analyze (data)
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


def _create_ambiguous_skills() -> list[SkillEntry]:
    """Create skills with overlapping keywords for disambiguation testing."""
    return [
        # Network proxy skill
        _make_skill(
            "proxy",
            "Proxy",
            "Configure network proxy settings and HTTP proxy servers. "
            "配置 网络代理 HTTP代理 服务器 转发 转发规则 端口",
        ),
        # AI Agent skill
        _make_skill(
            "agent",
            "Agent",
            "Create and manage AI agents for autonomous task execution. "
            "AI代理 智能代理 自动化 任务执行 人工智能 机器人",
        ),
        # Application deployment skill
        _make_skill(
            "deploy",
            "Deploy",
            "Deploy applications to servers and cloud platforms. "
            "部署 应用 服务器 云平台 发布 上线 容器 Docker K8s",
        ),
        # Data backup skill
        _make_skill(
            "backup",
            "Backup",
            "Create manage and restore data backups and snapshots. "
            "备份 数据 快照 恢复 存储 归档 灾难恢复",
        ),
        # System monitoring skill
        _make_skill(
            "monitor",
            "Monitor",
            "Monitor system health performance metrics and alerts. "
            "监控 系统 性能 指标 告警 CPU 内存 磁盘 网络",
        ),
        # Data analysis skill
        _make_skill(
            "analyze",
            "Analyze",
            "Analyze data patterns trends and generate insights. "
            "分析 数据 趋势 洞察 报告 可视化 统计 机器学习",
        ),
        # Code search skill
        _make_skill(
            "code-search",
            "Code Search",
            "Search and navigate source code repositories. "
            "搜索 代码 源码 仓库 函数 类 方法 重构",
        ),
        # Knowledge search skill
        _make_skill(
            "knowledge-search",
            "Knowledge Search",
            "Search knowledge base and documentation. "
            "搜索 知识库 文档 资料 信息 查询 检索",
        ),
        # Database query skill
        _make_skill(
            "db-query",
            "DB Query",
            "Execute database queries and manage data. "
            "数据库 查询 SQL 表 记录 数据 管理",
        ),
        # Data export skill
        _make_skill(
            "data-export",
            "Data Export",
            "Export data to various formats like CSV JSON Excel. "
            "导出 数据 CSV JSON Excel 格式 转换",
        ),
    ]


class TestProxyVsAgentDisambiguation:
    """Test disambiguation between proxy (network) and agent (AI)."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with ambiguous skills."""
        registry = SkillRegistry([])
        registry._entries = _create_ambiguous_skills()
        return SkillService(registry)

    def test_network_proxy_context(self, service: SkillService):
        """Network context should match proxy skill."""
        service.reset()
        route = service.maybe_auto_select("配置网络代理服务器")
        assert route is not None
        assert route.entry.workflow_id == "proxy"

    def test_http_proxy_context(self, service: SkillService):
        """HTTP context should match proxy skill."""
        service.reset()
        route = service.maybe_auto_select("设置HTTP代理")
        assert route is not None
        assert route.entry.workflow_id == "proxy"

    def test_proxy_server_context(self, service: SkillService):
        """Proxy server context should match proxy skill."""
        service.reset()
        route = service.maybe_auto_select("代理服务器配置")
        assert route is not None
        assert route.entry.workflow_id == "proxy"

    def test_ai_agent_context(self, service: SkillService):
        """AI context should match agent skill."""
        service.reset()
        route = service.maybe_auto_select("创建AI代理执行任务")
        assert route is not None
        assert route.entry.workflow_id == "agent"

    def test_intelligent_agent_context(self, service: SkillService):
        """Intelligent agent context should match agent skill."""
        service.reset()
        route = service.maybe_auto_select("智能代理自动化")
        assert route is not None
        assert route.entry.workflow_id == "agent"

    def test_autonomous_agent_context(self, service: SkillService):
        """Autonomous context should match agent skill."""
        service.reset()
        route = service.maybe_auto_select("自动化代理机器人")
        assert route is not None
        assert route.entry.workflow_id == "agent"


class TestDeployVsBackupDisambiguation:
    """Test disambiguation between deploy (app) and backup (data)."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with ambiguous skills."""
        registry = SkillRegistry([])
        registry._entries = _create_ambiguous_skills()
        return SkillService(registry)

    def test_app_deploy_context(self, service: SkillService):
        """Application context should match deploy skill."""
        service.reset()
        route = service.maybe_auto_select("部署应用到服务器")
        assert route is not None
        assert route.entry.workflow_id == "deploy"

    def test_docker_deploy_context(self, service: SkillService):
        """Docker context should match deploy skill."""
        service.reset()
        route = service.maybe_auto_select("部署Docker容器")
        assert route is not None
        assert route.entry.workflow_id == "deploy"

    def test_cloud_deploy_context(self, service: SkillService):
        """Cloud context should match deploy skill."""
        service.reset()
        route = service.maybe_auto_select("部署到云平台")
        assert route is not None
        assert route.entry.workflow_id == "deploy"

    def test_data_backup_context(self, service: SkillService):
        """Data context should match backup skill."""
        service.reset()
        route = service.maybe_auto_select("备份数据库")
        assert route is not None
        assert route.entry.workflow_id == "backup"

    def test_snapshot_backup_context(self, service: SkillService):
        """Snapshot context should match backup skill."""
        service.reset()
        route = service.maybe_auto_select("创建快照备份")
        assert route is not None
        assert route.entry.workflow_id == "backup"

    def test_disaster_recovery_context(self, service: SkillService):
        """Disaster recovery context should match backup skill."""
        service.reset()
        route = service.maybe_auto_select("灾难恢复备份")
        assert route is not None
        assert route.entry.workflow_id == "backup"


class TestMonitorVsAnalyzeDisambiguation:
    """Test disambiguation between monitor (system) and analyze (data)."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with ambiguous skills."""
        registry = SkillRegistry([])
        registry._entries = _create_ambiguous_skills()
        return SkillService(registry)

    def test_system_monitor_context(self, service: SkillService):
        """System context should match monitor skill."""
        service.reset()
        route = service.maybe_auto_select("监控系统性能")
        assert route is not None
        assert route.entry.workflow_id == "monitor"

    def test_cpu_monitor_context(self, service: SkillService):
        """CPU context should match monitor skill."""
        service.reset()
        route = service.maybe_auto_select("监控CPU使用率")
        assert route is not None
        assert route.entry.workflow_id == "monitor"

    def test_alert_monitor_context(self, service: SkillService):
        """Alert context should match monitor skill."""
        service.reset()
        route = service.maybe_auto_select("设置监控告警")
        assert route is not None
        assert route.entry.workflow_id == "monitor"

    def test_data_analyze_context(self, service: SkillService):
        """Data analysis context should match analyze skill."""
        service.reset()
        route = service.maybe_auto_select("分析数据趋势")
        assert route is not None
        assert route.entry.workflow_id == "analyze"

    def test_trend_analyze_context(self, service: SkillService):
        """Trend context should match analyze skill."""
        service.reset()
        route = service.maybe_auto_select("分析用户行为趋势")
        assert route is not None
        assert route.entry.workflow_id == "analyze"

    def test_insight_analyze_context(self, service: SkillService):
        """Insight context should match analyze skill."""
        service.reset()
        route = service.maybe_auto_select("生成数据洞察报告")
        assert route is not None
        assert route.entry.workflow_id == "analyze"


class TestSearchContextDisambiguation:
    """Test disambiguation between different search contexts."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with search-related skills."""
        registry = SkillRegistry([])
        registry._entries = _create_ambiguous_skills()
        return SkillService(registry)

    def test_code_search_context(self, service: SkillService):
        """Code context should match code-search skill."""
        service.reset()
        route = service.maybe_auto_select("搜索代码中的函数")
        assert route is not None
        assert route.entry.workflow_id == "code-search"

    def test_source_code_context(self, service: SkillService):
        """Source code context should match code-search skill."""
        service.reset()
        route = service.maybe_auto_select("在源码仓库中搜索")
        assert route is not None
        assert route.entry.workflow_id == "code-search"

    def test_knowledge_search_context(self, service: SkillService):
        """Knowledge context should match knowledge-search skill."""
        service.reset()
        route = service.maybe_auto_select("搜索知识库文档")
        assert route is not None
        assert route.entry.workflow_id == "knowledge-search"

    def test_documentation_context(self, service: SkillService):
        """Documentation context should match knowledge-search skill."""
        service.reset()
        route = service.maybe_auto_select("查找技术文档")
        assert route is not None
        assert route.entry.workflow_id == "knowledge-search"


class TestDatabaseVsExportDisambiguation:
    """Test disambiguation between database query and data export."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with data-related skills."""
        registry = SkillRegistry([])
        registry._entries = _create_ambiguous_skills()
        return SkillService(registry)

    def test_sql_query_context(self, service: SkillService):
        """SQL context should match db-query skill."""
        service.reset()
        route = service.maybe_auto_select("执行SQL查询")
        assert route is not None
        assert route.entry.workflow_id == "db-query"

    def test_table_management_context(self, service: SkillService):
        """Table context should match db-query skill."""
        service.reset()
        route = service.maybe_auto_select("管理数据库表")
        assert route is not None
        assert route.entry.workflow_id == "db-query"

    def test_csv_export_context(self, service: SkillService):
        """CSV context should match data-export skill."""
        service.reset()
        route = service.maybe_auto_select("导出数据为CSV格式")
        assert route is not None
        assert route.entry.workflow_id == "data-export"

    def test_json_export_context(self, service: SkillService):
        """JSON context should match data-export skill."""
        service.reset()
        route = service.maybe_auto_select("导出JSON文件")
        assert route is not None
        assert route.entry.workflow_id == "data-export"

    def test_excel_export_context(self, service: SkillService):
        """Excel context should match data-export skill."""
        service.reset()
        route = service.maybe_auto_select("导出Excel报表")
        assert route is not None
        assert route.entry.workflow_id == "data-export"


class TestAmbiguityResolution:
    """Test that ambiguous queries are handled appropriately."""

    @pytest.fixture
    def service(self) -> SkillService:
        """Create SkillService with ambiguous skills."""
        registry = SkillRegistry([])
        registry._entries = _create_ambiguous_skills()
        return SkillService(registry)

    def test_ambiguous_proxy_agent(self, service: SkillService):
        """Highly ambiguous query may return None or uncertain result."""
        service.reset()
        service.maybe_auto_select("代理")
        # This is ambiguous - could be proxy or agent
        # Router should either return None or mark as uncertain
        # The behavior depends on router implementation

    def test_ambiguous_deploy_backup(self, service: SkillService):
        """Highly ambiguous query may return None or uncertain result."""
        service.reset()
        service.maybe_auto_select("部署备份")
        # This is ambiguous - deploy or backup?
        # Router should handle gracefully

    def test_context_clarifies_intent(self, service: SkillService):
        """Additional context should clarify intent."""
        service.reset()
        service.maybe_auto_select("部署应用并备份数据库")
        # Has both deploy and backup keywords
        # Should pick one based on primary intent or return None

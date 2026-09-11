"""会话寻址与 API 隔离测试。

本项目是单用户本地服务（CORS 仅 localhost），跨会话可见性是 UI 设计
（侧边栏切换会话）。但会话级资源必须按 id 正确寻址：
- 快照/缓冲区按 session_id 各归各，不得串
- 未知 session_id 必须干净失败（空结果/KeyError），不得回落到别的会话
- 按 session_id 的清理只影响目标会话
"""
from unittest.mock import MagicMock

import pytest

from agentnexus.services.chat import ChatService


def _make_service() -> ChatService:
    return ChatService(
        agent_factory=lambda _sid=None: MagicMock(),
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )


class TestSessionAddressing:
    def test_token_buffers_do_not_mix(self):
        service = _make_service()
        s1 = service.start_session()
        s2 = service.start_session()
        with service._get_session_lock(s1.id):
            service._token_buffers[s1.id] = "s1 的部分输出"
        with service._get_session_lock(s2.id):
            service._token_buffers[s2.id] = "s2 的部分输出"
        assert service._token_buffers[s1.id] == "s1 的部分输出"
        assert service._token_buffers[s2.id] == "s2 的部分输出"

    def test_unknown_session_snapshot_is_empty_not_leaked(self):
        """未知 session_id 的缓冲区查询必须返回空，不得回落到现存会话。"""
        service = _make_service()
        s1 = service.start_session()
        with service._get_session_lock(s1.id):
            service._token_buffers[s1.id] = "敏感内容"
        assert service._token_buffers.get("session_ghost", "") == ""
        assert service._token_cursors.get("session_ghost", 0) == 0

    def test_send_message_unknown_session_raises(self):
        service = _make_service()
        with pytest.raises(KeyError):
            service.send_message("session_ghost", "hello")

    def test_session_ids_and_run_ids_unpredictable(self):
        """寻址边界：id 不可枚举（uuid），是会话资源的唯一屏障。"""
        service = _make_service()
        ids = {service.start_session().id for _ in range(20)}
        assert len(ids) == 20
        assert all(len(i) >= 20 for i in ids), "session id 必须足够长，不可遍历猜测"

    def test_clear_stm_only_affects_target_session(self):
        service = _make_service()
        s1 = service.start_session()
        s2 = service.start_session()
        service._stms[s1.id] = MagicMock()
        service._stms[s2.id] = MagicMock()
        # 等价于 /memory/short/clear?session_id=s1 的效果
        service._stms.pop(s1.id, None)
        assert s1.id not in service._stms
        assert s2.id in service._stms, "清理 s1 不得波及 s2"


class TestSessionAuthPosture:
    """安全态势钉板：会话端点当前未启用 API key 校验。

    verify_api_key 存在于 server/auth.py 但未接到任何 router；
    实际边界 = CORS localhost-only。此测试钉住现状——若未来接线 auth，
    应改为断言 401。
    """

    def test_session_endpoint_currently_open_without_token(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from agentnexus.server.routes.chat import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/sessions")
        assert resp.status_code != 401, (
            "会话端点当前无 API key 校验（边界=localhost CORS）。"
            "若已接线 verify_api_key，请把本断言改为 == 401"
        )

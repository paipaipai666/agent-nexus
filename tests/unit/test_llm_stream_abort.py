"""Tests for immediate LLM stream abortion on cancellation.

产品决策：断连或手动取消必须立刻停止所有行为。旧行为里 LLM 流只在
token 边界检查 cancel（re_act_agent 的逐 token 轮询），网络 stalls 时
取消不可见。这里验证：cancel 触发后 provider 的在途流被立即关闭、
think() 迅速以 AgentCancelled 退出。
"""

import threading
import time
from unittest.mock import PropertyMock, patch

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.core.capabilities import ModelCapabilities
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import BaseLLMProvider, StreamResult


class _StalledStreamProvider(BaseLLMProvider):
    """Simulates a stalled network stream.

    第一个 token 后立即阻塞（模拟网络 stall 期间的 read 等待）。
    abort_active_stream() 模拟关闭底层 httpx 连接：唤醒阻塞并让迭代报错。
    """

    def __init__(self):
        self.aborted = threading.Event()
        self.abort_calls = 0
        self.tokens = 0

    def stream_chat(
        self,
        *,
        messages,
        model,
        api_key,
        base_url,
        temperature=0,
        tools=None,
        response_format=None,
        max_tokens=None,
        timeout=60,
        parallel_tool_calls=None,
        stream_options=None,
        reasoning_effort=None,
        on_token=None,
    ) -> StreamResult:
        result = StreamResult()
        result.text = "start"
        if on_token:
            on_token("start")
        self.tokens += 1
        while not self.aborted.wait(timeout=0.1):
            # 阻塞直到 abort —— 模拟等待下一个网络 chunk。
            pass
        raise RuntimeError("stream closed by abort")

    def abort_active_stream(self) -> None:
        self.abort_calls += 1
        self.aborted.set()


def _make_llm() -> AgentLLM:
    return AgentLLM(
        model="fake/test-model",
        api_key="sk-test",
        base_url="http://127.0.0.1:9",
        timeout=5,
    )


def test_cancel_aborts_stalled_llm_stream_immediately():
    """cancel 触发后：provider 流被关闭，think() 以 AgentCancelled 退出。"""
    llm = _make_llm()
    provider = _StalledStreamProvider()
    cancel = threading.Event()
    llm.set_cancel_checker(cancel.is_set)

    outcome: list[str] = []

    def run():
        try:
            with patch("agentnexus.core.llm.select_provider", return_value=provider), \
                 patch.object(AgentLLM, "capabilities", new_callable=lambda: PropertyMock(return_value=ModelCapabilities())):
                llm.think([{"role": "user", "content": "hi"}])
            outcome.append("returned")
        except AgentCancelled:
            outcome.append("cancelled")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.5)  # let think() enter the stalled stream
    cancel.set()
    t.join(timeout=10)

    assert not t.is_alive(), "think() 未被取消唤醒——LLM 流仍阻塞在网络读取上"
    assert outcome == ["cancelled"], f"expected AgentCancelled, got {outcome}"
    assert provider.abort_calls >= 1, "cancel 后未触发 provider 的流关闭"


def test_llm_without_checker_keeps_stream_alive():
    """未装 cancel_checker 时（TUI 直跑等旧路径）流不受 watcher 影响。"""
    llm = _make_llm()
    provider = _StalledStreamProvider()

    done = threading.Event()
    outcome: list[str] = []

    def run():
        try:
            with patch("agentnexus.core.llm.select_provider", return_value=provider), \
                 patch.object(AgentLLM, "capabilities", new_callable=lambda: PropertyMock(return_value=ModelCapabilities())):
                llm.think([{"role": "user", "content": "hi"}])
            outcome.append("returned")
        except Exception as exc:  # noqa: BLE001
            outcome.append(type(exc).__name__)
        finally:
            done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=2)
    # 没有 checker，watcher 不存在，流照常阻塞（线程还活着）。
    assert not done.is_set(), "未装 checker 时流不应被中断"
    provider.aborted.set()  # 清理：让线程退出
    t.join(timeout=5)

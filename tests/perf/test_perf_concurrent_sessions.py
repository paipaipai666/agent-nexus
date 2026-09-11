"""Perf: 会话级并发基准——N 会话并行跑完整 run 的吞吐与正确性。

度量 ChatService 在多会话混合负载下的调度正确性（事件/答案各归各）
和并发收益（并行墙钟时间显著优于串行）。fake agent 每 run 睡 50ms
模拟 LLM 延迟，排除了真实模型抖动。
"""
import concurrent.futures
import threading
import time
from unittest.mock import MagicMock

CONCURRENT_RUNS = 16          # 4 会话 × 4 消息
FAKE_RUN_LATENCY_S = 0.05
# 串行基线 ≈ 16 × 50ms = 800ms；并发阈值给足 CI 余量
CONCURRENT_WALL_MAX_S = 0.9
EVENT_CROSS_TALK_ALLOWED = 0


def _fake_agent():
    agent = MagicMock()

    def _run(question, memory_manager=None, **kw):
        time.sleep(FAKE_RUN_LATENCY_S)
        result = MagicMock()
        result.answer = f"回答: {question}"
        result.steps = []
        return result

    agent.run.side_effect = _run
    agent.set_cancel_checker = MagicMock()
    return agent


def _make_service():
    from agentnexus.services.chat import ChatService
    return ChatService(
        agent_factory=lambda _sid=None: _fake_agent(),
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )


class TestConcurrentSessionPerf:
    def test_four_sessions_sixteen_runs(self):
        service = _make_service()
        sessions = [service.start_session() for _ in range(4)]
        errors: list[Exception] = []
        lock = threading.Lock()

        def do_run(session, j: int):
            try:
                run = service.send_message(session.id, f"问题{j}@{session.id}")
                events = list(service.stream_events(run.id))
                return session.id, j, run, events
            except Exception as e:
                with lock:
                    errors.append(e)
                return None

        # 串行基线
        t0 = time.perf_counter()
        r = do_run(sessions[0], 0)
        sequential_one = time.perf_counter() - t0
        assert r is not None

        # 并发 15 个（已跑 1 个）
        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_RUNS) as pool:
            futures = [
                pool.submit(do_run, sessions[i % 4], j)
                for i, j in [(s, q) for s in range(4) for q in range(4)
                             if not (s == 0 and q == 0)]
            ]
            results = [f.result() for f in futures]
        wall = time.perf_counter() - start

        assert not errors, f"并发运行异常: {errors}"
        assert all(r is not None for r in results)
        assert len(results) == CONCURRENT_RUNS - 1

        # 正确性：答案内容与会话归属一致（无串话）
        cross_talk = 0
        for sid, j, run, events in results:
            assert run.session_id == sid
            joined = " ".join(str(e.payload) for e in events)
            if f"问题{j}@{sid}" not in joined and events:
                cross_talk += 1
        assert cross_talk <= EVENT_CROSS_TALK_ALLOWED

        # 并发收益：15 个 50ms 任务在 4 路并行下应远快于串行
        print(f"\n单次 {sequential_one * 1000:.0f}ms, 并发 15 runs 墙钟 {wall:.2f}s")
        assert wall < CONCURRENT_WALL_MAX_S, (
            f"并发墙钟 {wall:.2f}s 超阈值 {CONCURRENT_WALL_MAX_S}s——调度未并行或被锁串行化"
        )

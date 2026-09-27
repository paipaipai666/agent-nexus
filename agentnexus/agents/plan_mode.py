"""Plan mode — per-session read-only enforcement and plan review.

When plan mode is active for a session, the agent may only call tools
marked ``read_only`` (see ``ToolMeta.read_only``); every other tool call is
blocked at ``ReActAgent._execute_tool``.  To leave plan mode the agent must
submit a final plan document via the ``exit_plan_mode`` tool; the document is
delivered to the user for approval and, on approval, the mode is disabled and
the plan is persisted under ``<workspace>/.agentnexus/plans/``.

Design constraints (do not violate):
- Approval must run on the agent run thread (the desktop HITL channel routes
  by run-thread id); therefore the gate lives in ``_execute_tool`` and
  ``exit_plan_mode`` must stay in the dispatcher's sequential group
  (``concurrency_safe=False``).
- Rejection or a failing confirm channel must never touch any state — a
  rejected exit leaves the gate fully in effect (cf. upstream agents whose
  gate silently died after a rejected exit).
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Callable

EXIT_PLAN_MODE_TOOL = "exit_plan_mode"
PLAN_REVIEW_MARKER = "[PLAN_REVIEW]"  # 前端据此渲染计划卡片，协议字面量勿改

PLAN_MODE_PROMPT = (
    "[计划模式 PLAN MODE — 当前会话状态]\n"
    "当前处于计划模式：你只能使用只读工具（检索/阅读/浏览类）。"
    "写入、执行、修改外部状态的工具都会被系统拦截。\n"
    "完成调研后必须调用 exit_plan_mode 工具，把完整的最终计划文档（Markdown）作为 plan 参数提交；"
    "计划会完整展示给用户审批，批准后计划模式自动退出，再按计划执行；被拒绝时修订后重新提交。\n"
    "计划模式下不要使用 todo_add/todo_update —— 把计划直接写进 plan 文档。"
)


class PlanModeManager:
    """线程安全的 per-session 计划模式开关集。disable/clear 幂等。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def enable(self, session_id: str) -> None:
        with self._lock:
            self._active.add(session_id)

    def disable(self, session_id: str) -> None:
        with self._lock:
            self._active.discard(session_id)

    def clear(self, session_id: str) -> None:
        """delete_session 清理用，与 disable 等价（幂等）。"""
        self.disable(session_id)

    def is_active(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active


class PlanModeBinding:
    """绑定到具体 session 的运行时视图，装在 per-session agent 上。

    ``active()`` 实时读 manager —— 用户在 run 中途手动开关立即生效，
    无需重装 binding。
    """

    def __init__(self, manager: PlanModeManager, session_id: str) -> None:
        self._manager = manager
        self._session_id = session_id

    def active(self) -> bool:
        return self._manager.is_active(self._session_id)

    def request_exit(self, plan: str, confirm: Callable[[str], bool]) -> str:
        """提交计划并申请退出。confirm 在 agent run 线程调用（桌面 HITL 路由依赖）。"""
        plan = plan.strip()
        if not plan:
            return "[exit_plan_mode] 计划文档不能为空。请把完整计划作为 plan 参数重新提交。"
        try:
            approved = confirm(f"{PLAN_REVIEW_MARKER}\n{plan}")
        except Exception as e:
            return f"[exit_plan_mode] 计划审批通道异常（{e}），按拒绝处理。请重试或请用户手动取消计划模式。"
        if not approved:
            return "[exit_plan_mode] 用户拒绝了该计划。请继续调研或修订计划后重新提交。"
        # 批准：先退出模式（落盘失败也不回滚），再 best-effort 落盘。
        self._manager.disable(self._session_id)
        save_note = self._persist_plan(plan)
        return (
            "[exit_plan_mode] 计划已获批准，计划模式已退出。"
            f"{save_note}现在可以执行写入工具，请按计划实施。"
        )

    def _persist_plan(self, plan: str) -> str:
        """把批准的计划写入 <workspace>/.agentnexus/plans/。返回给提示文案用的说明。"""
        try:
            from agentnexus.tools.workspace import get_effective_workspace
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = get_effective_workspace() / ".agentnexus" / "plans" / f"plan-{ts}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(plan + "\n", encoding="utf-8")
            return f"计划已保存到 {path}。"
        except Exception as e:
            return f"（计划保存失败: {e}）"

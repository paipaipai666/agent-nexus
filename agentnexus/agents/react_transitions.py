"""Transfer table for the redesigned ReAct FSM — 13 transition rules.

形态：6 状态 × 封闭事件集。决策函数（agentnexus.agents.decisions）的
返回值只有几种，因此这张表是全函数——任何 handler 返回值都能在落点
状态找到对应的行，"no transition" 崩溃在结构上不可能发生
（totality 由 tests/unit/test_fsm_table_totality.py 断言）。

行语义：handler 在**落点状态**执行（见 fsm.py run_loop）。
"""

from agentnexus.agents.react_types import ReActEventType as E
from agentnexus.agents.react_types import ReActState as S
from agentnexus.agents.react_types import Transition

TRANSFER_TABLE: list[Transition] = [
    # ── INIT ──
    Transition(S.INIT, E.START, S.AWAIT_MODEL, "_on_init"),

    # ── AWAIT_MODEL ──
    # auto-advance（event=None）：一轮模型往返 + 解释。handler 返回
    # TOOLS_REQUESTED / ANSWER_READY / FAULT 之一时转出本状态；
    # 返回 ROUND_READY（截断整批失败等"直接再来一轮"的情形）自环。
    Transition(S.AWAIT_MODEL, None, S.AWAIT_MODEL, "_on_round"),
    Transition(S.AWAIT_MODEL, E.ROUND_READY, S.AWAIT_MODEL, "_on_round_advance"),
    Transition(S.AWAIT_MODEL, E.TOOLS_REQUESTED, S.EXECUTE_TOOL, "_on_tools_requested"),
    Transition(S.AWAIT_MODEL, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
    Transition(S.AWAIT_MODEL, E.FAULT, S.RECOVER, "_on_recover"),

    # ── EXECUTE_TOOL ──
    Transition(S.EXECUTE_TOOL, E.TOOLS_DONE, S.AWAIT_MODEL, "_on_round_advance"),
    Transition(S.EXECUTE_TOOL, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
    # Durable commit failure after tool batch — do not enter the next round.
    Transition(S.EXECUTE_TOOL, E.FAULT, S.RECOVER, "_on_recover"),

    # ── RECOVER ──
    # _on_recover 的决策封闭于 {ROUND_READY, ANSWER_READY, ABORT}。
    Transition(S.RECOVER, E.ROUND_READY, S.AWAIT_MODEL, "_on_round_advance"),
    Transition(S.RECOVER, E.ANSWER_READY, S.ANSWER, "_on_answer_ready"),
    Transition(S.RECOVER, E.ABORT, S.DONE, "_on_error_abort"),

    # ── ANSWER ──
    Transition(S.ANSWER, E.ANSWER_VETOED, S.AWAIT_MODEL, "_on_vetoed"),
    # (unconditional: always → DONE)
    Transition(S.ANSWER, None, S.DONE, "_on_emit_answer"),
]

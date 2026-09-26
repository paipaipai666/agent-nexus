"""转移表 totality 守护测试（AST 静态审计，固化为 CI 门禁）。

背景：这张表最初不是全函数——handler 在落点状态发出了该状态没定义的事件，
`fsm.py` 直接抛 FSMError 让整轮报废。三个这样的缺口都能被普通模型行为触发。
`scripts/audit_fsm.py` 是当初用来挖它们的临时脚本，现在固化成测试：
**以后任何新 handler 造成同样的缺口，跑测试就会挂**，而不是等到线上崩。

判定方式（见 `_queued_events_in`）：
- 只统计 `ReActEvent(ReActEventType.X, ...)` 这类**进队列**的构造
- `ctx.emit(ReActEventType.X, ...)` 是 TUI 旁路，不进队列，不算缺口
- `RetryReason.X` 被排除：它有几个成员与事件同名（THOUGHT_MISSING /
  EMPTY_RESPONSE / PARSE_ERROR），早期版本因此误报 6 条
"""
import ast
import pathlib

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_transitions import TRANSFER_TABLE
from agentnexus.agents.react_types import ReActEventType as E
from agentnexus.agents.react_types import ReActState as S

PKG = pathlib.Path(ReActAgent.__module__.replace(".", "/")).parent.parent
MODULES = {
    "re_act_agent": PKG / "agents/re_act_agent.py",
    "react_runtime": PKG / "agents/react_runtime.py",
}
TREES = {name: ast.parse(path.read_text(encoding="utf-8"))
         for name, path in MODULES.items()}

# 决策函数里可能构造事件的辅助函数前缀（有界传递解析）。
# 注意 `_fail_` / `_degrade_` / `_maybe_` 必须包含：_on_round 的 ROUND_READY
# 就是经 _fail_truncated_tool_calls 返回的，漏掉会形成审计盲点。
_HELPER_PREFIXES = (
    "_on_", "_fail_", "_degrade_", "_maybe_", "_record_", "_emit_",
    "execute_", "record_", "retry_", "parse_",
)


def _queued_events_in(node) -> tuple[set[str], set[str]]:
    """返回 (队列事件, 旁路事件) 两个集合。"""
    queue, side = set(), set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        fname = f.attr if isinstance(f, ast.Attribute) else (
            f.id if isinstance(f, ast.Name) else None)
        if not sub.args:
            continue
        arg = sub.args[0]
        if not (isinstance(arg, ast.Attribute) and arg.attr in E.__members__
                and getattr(arg.value, "id", "") in ("ReActEventType", "E")):
            continue
        if fname == "ReActEvent":
            queue.add(arg.attr)
        elif fname == "emit":
            side.add(arg.attr)
    return queue, side


class _EmissionResolver:
    """handler -> 它可能 return 的事件类型（有界传递解析）。"""

    def __init__(self):
        self.cache: dict = {}

    def resolve(self, name: str, depth: int = 0, seen: set | None = None):
        seen = seen or set()
        key = (name, depth)
        if key in self.cache:
            return self.cache[key]
        if name in seen or depth > 2:
            return set(), set()
        seen = seen | {name}
        queue, side = set(), set()
        for mod, tree in TREES.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    q, s = _queued_events_in(node)
                    queue |= q
                    side |= s
                    for call in ast.walk(node):
                        if isinstance(call, ast.Call):
                            callee = call.func
                            cname = (
                                callee.attr if isinstance(callee, ast.Attribute)
                                else callee.id if isinstance(callee, ast.Name)
                                else None
                            )
                            if cname and cname.startswith(_HELPER_PREFIXES):
                                sub = self.resolve(cname, depth + 1, seen)
                                queue |= sub[0]
                                side |= sub[1]
        self.cache[key] = (queue, side)
        return self.cache[key]


_RESOLVER = _EmissionResolver()


def _find_gaps() -> list[tuple[S, E | None, S, str, str]]:
    """返回 [(from_state, event, landing_state, handler, 无法消费的事件)]。"""
    lookup = {(t.state, t.event): t for t in TRANSFER_TABLE}
    unconditional = {t.state for t in TRANSFER_TABLE if t.event is None}
    gaps = []
    for t in TRANSFER_TABLE:
        if t.next_state == S.DONE:
            continue
        queued, _side = _RESOLVER.resolve(t.handler)
        for name in sorted(queued):
            ev = E[name]
            if (t.next_state, ev) in lookup or t.next_state in unconditional:
                continue
            gaps.append((t.state, t.event, t.next_state, t.handler, name))
    return gaps


class TestTableTotality:
    def test_no_handler_emits_an_event_its_landing_state_cannot_consume(self):
        """核心不变量：handler 的返回值封闭集 ⊆ 落点状态的行集合。"""
        gaps = _find_gaps()
        assert not gaps, (
            "以下转移会让落点状态收到它没有定义的事件（fsm.py 会抛 FSMError）：\n"
            + "\n".join(
                f"  {s.name} + {e.name if e else '<uncond>'} -> {nxt.name} "
                f"| {handler}() 返回 {ev}"
                for s, e, nxt, handler, ev in gaps
            )
        )

    def test_every_handler_named_by_the_table_exists(self):
        """表里的每个 handler 名都必须在 ReActAgent 上存在（引擎启动前的第二道闸）。"""
        missing = sorted({t.handler for t in TRANSFER_TABLE} - set(dir(ReActAgent)))
        assert not missing, f"转移表引用了不存在的 handler: {missing}"

    def test_state_set_is_six_states(self):
        assert [s.name for s in S] == [
            "INIT", "AWAIT_MODEL", "EXECUTE_TOOL", "RECOVER", "ANSWER", "DONE",
        ]

    def test_table_stays_small(self):
        """护栏：表不该重新膨胀。33 行是旧形态；放宽到 20 是给未来留出空间。"""
        assert len(TRANSFER_TABLE) <= 20, \
            f"转移表已膨胀到 {len(TRANSFER_TABLE)} 行——多半是又把流水线步骤当成状态了"


class TestDecisionClosure:
    """每个状态的出口集合 == 其 handler 的返回值封闭集（totality 的正面表述）。"""

    def _rows_for(self, state: S):
        return [t for t in TRANSFER_TABLE if t.state == state]

    def _emitted_by_handlers_landing_in(self, state: S) -> set[E]:
        emitted: set[E] = set()
        for t in TRANSFER_TABLE:
            if t.next_state != state:
                continue
            queued, _side = _RESOLVER.resolve(t.handler)
            emitted |= {E[n] for n in queued}
        return emitted

    def test_await_model_is_closed(self):
        rows = {t.event for t in self._rows_for(S.AWAIT_MODEL)}
        assert rows == {
            None, E.TOOLS_REQUESTED, E.ANSWER_READY, E.FAULT, E.ROUND_READY,
        }
        assert self._emitted_by_handlers_landing_in(S.AWAIT_MODEL) <= rows

    def test_recover_is_closed(self):
        rows = {t.event for t in self._rows_for(S.RECOVER)}
        assert rows == {E.ROUND_READY, E.ANSWER_READY, E.ABORT}
        assert self._emitted_by_handlers_landing_in(S.RECOVER) <= rows

    def test_execute_tool_is_closed(self):
        rows = {t.event for t in self._rows_for(S.EXECUTE_TOOL)}
        assert rows == {E.TOOLS_DONE, E.ANSWER_READY}
        assert self._emitted_by_handlers_landing_in(S.EXECUTE_TOOL) <= rows

    def test_answer_is_total(self):
        rows = {t.event for t in self._rows_for(S.ANSWER)}
        assert rows == {E.ANSWER_VETOED, None}

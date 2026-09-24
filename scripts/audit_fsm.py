"""Static audit of the ReAct FSM transition table.

For every row (state, event, next_state, handler) we ask: after landing in
`next_state`, can the emissions of `handler` actually be consumed?

Emissions are resolved transitively: handler -> helper functions in
react_runtime / re_act_agent that build ReActEvent objects.
"""
import ast
import inspect
import pathlib

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.agents.react_transitions import TRANSFER_TABLE
from agentnexus.agents.react_types import ReActEventType as E, ReActState as S

PKG = pathlib.Path(ReActAgent.__module__.replace(".", "/")).parent.parent
MODULES = {
    "re_act_agent": PKG / "agents/re_act_agent.py",
    "react_runtime": PKG / "agents/react_runtime.py",
}


def _module_tree(name):
    return ast.parse(MODULES[name].read_text(encoding="utf-8"))


TREES = {name: _module_tree(name) for name in MODULES}


def _events_in(node) -> tuple[set[str], set[str]]:
    """Collect (queue_events, side_channel_events) built inside an AST node.

    - queue events:  ReActEvent(ReActEventType.X, ...)   -> returned by handlers
    - side channel:  ctx.emit(ReActEventType.X, ...)     -> TUI only, never queued
    `RetryReason.X` is deliberately skipped: several members share names with
    ReActEventType and caused false positives.
    """
    queue, side = set(), set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        fname = None
        if isinstance(f, ast.Attribute):
            fname = f.attr
        elif isinstance(f, ast.Name):
            fname = f.id
        if fname == "ReActEvent" and sub.args:
            arg = sub.args[0]
            if isinstance(arg, ast.Attribute) and arg.attr in E.__members__ \
                    and getattr(arg.value, "id", "") in ("ReActEventType", "E"):
                queue.add(arg.attr)
        elif fname == "emit" and sub.args:
            arg = sub.args[0]
            if isinstance(arg, ast.Attribute) and arg.attr in E.__members__ \
                    and getattr(arg.value, "id", "") in ("ReActEventType", "E"):
                side.add(arg.attr)
    return queue, side


def _find_func(name, want_module=None):
    for mod, tree in TREES.items():
        if want_module and mod != want_module:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                yield mod, node


class EmissionResolver:
    """handler -> event types it may emit (with bounded transitive resolution)."""

    def __init__(self):
        self.cache = {}

    def resolve(self, name, depth=0, seen=None):
        seen = seen or set()
        key = (name, depth)
        if key in self.cache:
            return self.cache[key]
        if name in seen or depth > 2:
            return set()
        seen = seen | {name}
        queue, side = set(), set()
        for mod, fn in _find_func(name):
            q, s = _events_in(fn)
            queue |= q
            side |= s
            # follow calls to sibling helpers that build events
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    callee = call.func
                    cname = callee.attr if isinstance(callee, ast.Attribute) else (
                        callee.id if isinstance(callee, ast.Name) else None)
                    if cname and cname.startswith(("_on_", "execute_", "record_", "retry_", "parse_")):
                        sub = self.resolve(cname, depth + 1, seen)
                        queue |= sub[0]
                        side |= sub[1]
        self.cache[key] = (queue, side)
        return self.cache[key]


resolver = EmissionResolver()

lookup = {(t.state, t.event): t for t in TRANSFER_TABLE}
nones = {t.state for t in TRANSFER_TABLE if t.event is None}

print("=" * 78)
print(f"table rows = {len(TRANSFER_TABLE)}   states used = "
      f"{len({t.state for t in TRANSFER_TABLE})} / {len(S)}   "
      f"events used = {len({t.event for t in TRANSFER_TABLE if t.event})}/{len(E)}")
print(f"states with an unconditional fallback row: {sorted(s.name for s in nones)}")
print("=" * 78)

# 1. Handler emissions that the landing state cannot consume
print("\n[1] rows whose landing state cannot consume what the handler RETURNS")
print("    (ctx.emit is a TUI side-channel and is excluded)")
problems = []
for t in TRANSFER_TABLE:
    if t.next_state == S.DONE:
        continue
    queued, _ = resolver.resolve(t.handler)
    for ev_name in sorted(queued):
        ev = E[ev_name]
        if (t.next_state, ev) in lookup or t.next_state in nones:
            continue
        problems.append((t.state, t.event, t.next_state, t.handler, ev_name))

for s, e, nxt, handler, ev in problems:
    print(f"  {s.name} + {e.name if e else '<uncond>'} -> {nxt.name} "
          f"| {handler}() returns {ev} | NO ROW ({nxt.name}+{ev}) -> FSMError")
print(f"  -> {len(problems)} gap(s)")

# 2. States that can be entered but have very few exits
print("\n[2] exits per state")
for s in sorted({t.state for t in TRANSFER_TABLE}, key=lambda x: x.name):
    rows = [t for t in TRANSFER_TABLE if t.state == s]
    marks = ", ".join((f"{t.event.name if t.event else '<uncond>'}") for t in rows)
    print(f"  {s.name:<16} {len(rows)} exit(s): {marks}")

# 3. Events never used by the table (dead protocol surface)
used = {t.event for t in TRANSFER_TABLE if t.event}
print("\n[3] event types the table never reacts to (emitted only as UI side-channel?)")
print("   ", ", ".join(sorted(e.name for e in E if e not in used)))

# 4. Which states are pure pipeline (single entry, single meaningful exit)
print("\n[4] states with exactly one exit = straight-line steps, not real decision points")
pipeline = [t.state.name for s in {t.state for t in TRANSFER_TABLE}
            if len([r for r in TRANSFER_TABLE if r.state == s]) == 1]
print("   ", ", ".join(sorted(pipeline)))

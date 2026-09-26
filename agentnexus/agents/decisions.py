"""Pure decision functions for the ReAct loop (FSM redesign Step 1).

Every function here is side-effect free: it reads plain values and returns a
decision object.  The agent handlers keep all mutations (messages, memory,
output, session_caps) and translate decisions into FSM events using the
CURRENT transition table — so rewiring changes no behavior, including the
three known crash gaps (fixed later in Step 2/3).

This module is the future home of the two closed decision types that make
the redesigned table a total function by construction:

    interpret() -> ModelDecision   # kind in {tools, answer, fault}
    recover()  -> RecoverDecision  # kind in {round, salvage, abort}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from agentnexus.agents import json_helpers
from agentnexus.agents.react_types import CallingStrategy, RetryReason

# ── constants (moved from re_act_agent, re-exported there) ────────────

# Native 模式下部分模型（如 OpenRouter stealth 系列）习惯在可见文本里写
# "Thought: <分析> <答案>" 或 "Thought: <分析> 最终答案: <答案>"。
_THOUGHT_MARKER_RE = re.compile(
    r"^\s*[*_]{0,2}\s*(?:thought|thinking|分析思考|思考过程|思考|想法|分析)\s*[*_]{0,2}\s*[:：]\s*",
    re.IGNORECASE,
)
_FINAL_ANSWER_RE = re.compile(
    r"[*_]{0,2}\s*(?:最终答案|final\s*answer)\s*[*_]{0,2}\s*[:：]\s*",
    re.IGNORECASE,
)

# Tools whose results are pure side effects — their observations can never
# change the final answer, so a batch containing ONLY these can fast-path
# to EMIT_ANSWER when the response already carries substantive text.
_BOOKKEEPING_TOOLS = frozenset({"todo_add", "todo_update"})

# Minimum visible-text length for the terminal fast path. Short notes like
# "我先更新下待办" stay on the normal loop; only answer-grade text qualifies.
_TERMINAL_TEXT_MIN_CHARS = 30


def split_native_thought_answer(text: str) -> tuple[str, str]:
    """把模型的可见文本拆成 (thought, answer)。

    - 无 Thought 标记 → ("", 原文)，整段视为答案。
    - 含显式答案标记（最终答案:/Final Answer:）→ 在标记处切分。
    - 只有 Thought 标记 → 首句为思考、其余为答案；若首句后没有余文，
      则整段都是思考、答案为空（调用方回退为去标记原文，避免重复展示）。
    """
    t = (text or "").strip()
    if not t:
        return "", ""
    m = _THOUGHT_MARKER_RE.match(t)
    if not m:
        return "", t
    body = t[m.end():]
    am = _FINAL_ANSWER_RE.search(body)
    if am:
        return body[:am.start()].strip(), body[am.end():].strip()
    sm = re.search(r"[。！？!?]", body)
    if sm:
        head = body[:sm.end()].strip()
        rest = body[sm.end():].strip()
        if rest:
            return head, rest
    return body, ""


# ── decision types ─────────────────────────────────────────────────────


@dataclass
class ModelDecision:
    """Interpretation of one model response (native or JSON protocol).

    kind ∈ {tools, answer, fault}.  Since Step 2, tool_calls and text may
    coexist: kind is "tools" when the model asked for tools, and `text`
    carries any commentary the model spoke alongside (shown to the user as
    an ANSWER_THOUGHT side-channel before the batch runs).
    """

    kind: str                    # "tools" | "answer" | "fault"
    tool_calls: list[dict] = field(default_factory=list)
    thought: str = ""
    text: str = ""               # answer text (kind=answer) or commentary (kind=tools)
    terminal_answer: str | None = None   # bookkeeping fast-path stash
    reason: RetryReason | None = None    # kind=fault
    detail: str = ""                     # kind=fault
    # fault sub-flags — handler-side side effects to perform:
    fail_pending_calls: bool = False     # truncated with pending calls (pi semantics)
    no_tools_no_text: bool = False       # native: degrade tool_calling
    recovered_protocol_json: bool = False
    # answer display hints (handler decides memory/TUI side effects):
    persist_reasoning: str = ""          # streamed reasoning to persist
    display_thought: str = ""            # pre-answer thought to display


@dataclass
class RecoverDecision:
    kind: str                    # "round" | "salvage" | "degrade" | "abort"
    reason: RetryReason | None = None
    detail: str = ""


# ── interpret: native path (mirrors the old _on_receive_native branching) ──


def interpret_native(
    *,
    response_text: str,
    reasoning_text: str,
    tool_calls: list[dict] | None,
    truncated: bool,
    streamed: bool,
    tool_exists: Callable[[str], bool],
) -> ModelDecision:
    """Classify one native-tool-call response. Faithful copy of the current
    branching in ReActAgent._on_receive_native — including the Thought gate.
    """
    calls = tool_calls if isinstance(tool_calls, list) else []

    # Truncation (finish_reason=length): never execute possibly-broken calls.
    if truncated:
        if calls:
            return ModelDecision(
                kind="fault", reason=RetryReason.TRUNCATED,
                detail="finish_reason=length", fail_pending_calls=True,
            )
        return ModelDecision(kind="fault", reason=RetryReason.TRUNCATED,
                             detail="finish_reason=length")

    if calls:
        # 决策1（2026-09-24 拍板）：允许模型不做可见思考直接调工具。
        # thought 只是展示材料，不再作为门禁；为空时 handler 跳过展示。
        thought = select_visible_thought(response_text, reasoning_text)
        terminal_text = (response_text or "").strip()
        terminal_answer = None
        if (
            len(terminal_text) >= _TERMINAL_TEXT_MIN_CHARS
            and all(tc.get("name") in _BOOKKEEPING_TOOLS for tc in calls)
        ):
            terminal_answer = terminal_text
        return ModelDecision(
            kind="tools", tool_calls=list(calls), thought=thought,
            text=terminal_text, terminal_answer=terminal_answer,
        )

    # ── no tool calls ──
    text = response_text or reasoning_text
    if not text:
        return ModelDecision(kind="fault", no_tools_no_text=True)

    recovered = _recover_protocol_json(
        response_text=response_text, tool_exists=tool_exists,
    )
    if recovered is not None:
        return recovered

    reasoning = (reasoning_text or "").strip()
    response = (response_text or "").strip()

    decision = ModelDecision(kind="answer")
    if streamed:
        decision.persist_reasoning = reasoning
    elif reasoning and response:
        decision.display_thought = reasoning
    else:
        thought, answer = split_native_thought_answer(text)
        if thought and answer:
            decision.display_thought = thought

    if reasoning and not streamed and response:
        decision.text = response
    else:
        _, ans = split_native_thought_answer(text)
        decision.text = ans or _THOUGHT_MARKER_RE.sub("", text, count=1).strip() or text
    return decision


def _recover_protocol_json(
    *,
    response_text: str,
    tool_exists: Callable[[str], bool],
) -> ModelDecision | None:
    """模型把 ReAct 协议 JSON 写进正文（native 通道漏接）时还原语义。

    - {"tool": ..., "params": ...} 且工具存在 → 还原为真实工具调用继续执行
    - 显式 {"answer": ...} → 提取答案文本
    返回 None 表示正文不是协议 JSON（含普通 JSON 数据），按原样作答。
    """
    text = response_text
    if not text or "{" not in text:
        return None
    parsed = json_helpers.robust_json_parse(text)
    ptype = parsed.get("type")
    if ptype == "tool_call" and tool_exists(parsed["tool"]):
        return ModelDecision(
            kind="tools",
            tool_calls=[{"name": parsed["tool"], "arguments": parsed["params"]}],
            thought="", recovered_protocol_json=True,
        )
    # 仅接受显式 "answer" 键——单键/多键数据 JSON（如 {"温度": "26°C"}）原样保留
    if ptype == "answer" and '"answer"' in text:
        return ModelDecision(
            kind="answer", text=parsed["text"],
            recovered_protocol_json=True,
        )
    return None


def select_visible_thought(response_text: str, reasoning_text: str) -> str:
    """Pick the Thought text to show for a tool-call round (mirrors
    ReActAgent._select_visible_thought).  Pure — no capability flags here;
    the agent wrapper caches results on the instance.
    """
    reasoning = (reasoning_text or "").strip()
    if reasoning:
        return reasoning

    text = (response_text or "").strip()
    if not text:
        return ""

    parsed = json_helpers.try_fix_json(text)
    if isinstance(parsed, dict):
        thought = str(parsed.get("thought", "")).strip()
        if thought:
            return thought
        if "tool" in parsed or "answer" in parsed or "params" in parsed:
            return ""

    m = _THOUGHT_MARKER_RE.match(text)
    if m:
        body = text[m.end():]
        am = _FINAL_ANSWER_RE.search(body)
        if am:
            return body[:am.start()].strip()
        return body
    return text


# ── interpret: JSON protocol path (mirrors CHECK_EMPTY → JSON_PARSE → CLASSIFY) ──


def interpret_json(*, response_text: str, truncated: bool) -> ModelDecision:
    """Interpret one JSON-protocol response (fused pipeline).

    Mirrors the old CHECK_EMPTY → JSON_PARSE → CLASSIFY state chain:
    empty → fault(EMPTY_RESPONSE); truncated → fault(TRUNCATED);
    parse error → fault(PARSE_ERROR); classified tool/answer/error.
    """
    if not response_text:
        return ModelDecision(kind="fault", reason=RetryReason.EMPTY_RESPONSE)
    if truncated:
        return ModelDecision(kind="fault", reason=RetryReason.TRUNCATED,
                             detail="finish_reason=length")
    parsed = json_helpers.robust_json_parse(response_text)
    if parsed.get("type") == "error":
        return ModelDecision(kind="fault", reason=RetryReason.PARSE_ERROR,
                             detail=str(parsed.get("reason", "")))
    ptype = parsed.get("type")
    if ptype == "tool_call":
        return ModelDecision(
            kind="tools",
            tool_calls=[{"name": parsed["tool"], "arguments": parsed["params"]}],
            thought=parsed.get("thought", ""),
        )
    if ptype == "answer":
        return ModelDecision(kind="answer", text=parsed["text"])
    return ModelDecision(kind="fault", reason=RetryReason.CLASSIFY_ERROR,
                         detail=str(parsed.get("reason", "unknown")))


# ── recover (mirrors react_runtime.retry_gate + _on_retries_left tail) ──


def retry_gate_decision(
    *,
    json_retries: int,
    max_json_retries: int,
    strategy: CallingStrategy,
) -> str:
    """RETRY_GATE: "round" (RETRIES_LEFT) | "salvage" (FALLBACK_TEXT) |
    "degrade" (NO_RETRIES).  Mirrors react_runtime.retry_gate.
    """
    if json_retries < max_json_retries:
        return "round"
    if strategy == CallingStrategy.JSON_MODE:
        return "degrade"
    return "salvage"


def select_strategy(session_caps: Any, caps: Any) -> CallingStrategy:
    """Mirrors ReActAgent._select_strategy — capability ladder with session
    blocklist.  Kept as a free function so decisions stay framework-free.
    """
    if session_caps.is_available("tool_calling", caps.supports_tool_calling):
        return CallingStrategy.NATIVE_TOOLS
    if session_caps.is_available("json_mode", caps.supports_json_mode):
        return CallingStrategy.JSON_MODE
    return CallingStrategy.PROMPT_JSON

"""Memory backends under test.

A backend answers questions over a conversation it has ingested:

    backend.reset()                    # fresh state per conversation
    backend.ingest(turns)              # replay the conversation chronologically
    answer = backend.answer(question)  # produce a final answer

Two implementations:

- ``NexusBackend`` — the real product: turns flow through MemoryManager
  (append → conclude → extraction/LTM), answers use init_session() recall.
- ``NaiveBackend`` — no memory at all; the full transcript is stuffed into the
  prompt. This is the full-context reference point every memory system claims
  to beat (and doubles as a no-memory control).
"""

from __future__ import annotations

from typing import Any, Protocol

from agentnexus.eval.memory.schema import Turn

SYSTEM_PROMPT = "你是用户的长期项目助手，根据掌握的背景信息回答问题，保持简洁。"


class MemoryBackend(Protocol):
    name: str

    def reset(self) -> None: ...
    def ingest(self, turns: list[Turn]) -> None: ...
    def answer(self, question: str) -> str: ...
    def prompt_chars(self) -> int: ...
    def close(self) -> None: ...


class BaseBackend:
    name = "base"
    _generate: Any  # llm with .think(messages, silent=True)

    def __init__(self, generate_llm):
        self._generate = generate_llm
        self._last_prompt_chars = 0
        self._last_context = ""

    def prompt_chars(self) -> int:
        return self._last_prompt_chars

    def last_context(self) -> str:
        """Memory context injected for the most recent answer() call.

        The retrieval-level (model-free) measurement reads this to check gold
        coverage. Empty string for backends without a retrieval stage.
        """
        return self._last_context

    def close(self) -> None:
        pass

    def _ask(self, system: str, question: str) -> str:
        self._last_prompt_chars = len(system) + len(question)
        return self._generate.think(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            silent=True,
        )


class NaiveBackend(BaseBackend):
    """Full-context baseline: everything stays in the prompt, no memory."""

    name = "naive"

    def __init__(self, generate_llm, max_turn_chars: int = 200_000):
        super().__init__(generate_llm)
        self._max_turn_chars = max_turn_chars
        self._transcript = ""

    def reset(self) -> None:
        self._transcript = ""

    def ingest(self, turns: list[Turn]) -> None:
        kept: list[str] = []
        total = 0
        for t in reversed(turns):  # newest wins if the budget overflows
            if total + len(t.content) > self._max_turn_chars:
                break
            kept.append(f"{t.role}: {t.content}")
            total += len(t.content)
        self._transcript = "\n".join(reversed(kept))

    def answer(self, question: str) -> str:
        system = f"{SYSTEM_PROMPT}\n\n对话记录:\n{self._transcript}" if self._transcript \
            else SYSTEM_PROMPT
        self._last_context = self._transcript   # full history IS its "retrieval"
        return self._ask(system, question)


class NexusBackend(BaseBackend):
    """The production memory system, driven through its public manager API.

    Each conversation runs in an isolated MemorySandbox (fresh AGENTNEXUS_HOME,
    real SQLite+Chroma LTM) so suites are reproducible and CI-safe.
    """

    name = "nexus"

    def __init__(self, generate_llm):
        super().__init__(generate_llm)
        self._sandbox = None
        self._mgr = None
        self._pending_user: str | None = None

    def _ensure_sandbox(self):
        if self._sandbox is None:
            from agentnexus.evaluation.memory_eval import MemorySandbox

            self._sandbox = MemorySandbox()
            self._mgr = self._build_manager()

    def _build_manager(self):
        from agentnexus.memory.manager import MemoryManager

        return MemoryManager(
            session_id="memory-bench",
            llm=self._generate,
            enable_long_term=True,
        )

    def reset(self) -> None:
        if self._sandbox is not None:
            self._sandbox.close()
        self._sandbox = None
        self._mgr = None
        self._pending_user = None

    def ingest(self, turns: list[Turn]) -> None:
        self._ensure_sandbox()
        for t in turns:
            self._mgr.append(t.role, t.content)
            # Production write path: conclude() per user/assistant exchange
            # runs the admission gate and extraction into LTM.
            if t.role == "user":
                self._pending_user = t.content
            elif self._pending_user is not None:
                self._mgr.conclude(self._pending_user, t.content)
                self._pending_user = None

    def answer(self, question: str) -> str:
        self._ensure_sandbox()
        context = self._mgr.init_session(question)
        self._last_context = context
        system = f"{SYSTEM_PROMPT}\n\n{context}" if context else SYSTEM_PROMPT
        return self._ask(system, question)

    def close(self) -> None:
        if self._sandbox is not None:
            self._sandbox.close()
            self._sandbox = None
            self._mgr = None

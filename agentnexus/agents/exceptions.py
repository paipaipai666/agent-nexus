class AgentNexusError(Exception):
    """Base exception for all AgentNexus domain errors."""


class FSMError(AgentNexusError):
    """Agent FSM table/dispatch invariant violated (programming error)."""


class AgentCancelled(AgentNexusError):
    """User/caller-initiated cancellation of an agent run."""


class MemoryCommitError(AgentNexusError):
    """Durable memory commit failed after bounded retries.

    Raised instead of silently continuing: a failed write must not start
    the next ReAct round (data would drift from disk).
    """

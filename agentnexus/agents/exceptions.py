class AgentNexusError(Exception):
    """Base exception for all AgentNexus domain errors."""


class FSMError(AgentNexusError):
    """Agent FSM table/dispatch invariant violated (programming error)."""


class AgentCancelled(AgentNexusError):
    """User/caller-initiated cancellation of an agent run."""

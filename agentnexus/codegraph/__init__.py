"""Code knowledge graph for structured code understanding.

Provides AST-based code parsing, graph storage, semantic search,
and agent tool integration for navigating codebases.
"""

__version__ = "0.1.0"

from agentnexus.codegraph.models import (
    EdgeData,
    EdgeKind,
    NodeData,
    NodeKind,
    ParseResult,
    build_embedding_text,
    make_module_qualname,
    make_node_id,
)

__all__ = [
    "__version__",
    "NodeKind",
    "EdgeKind",
    "NodeData",
    "EdgeData",
    "ParseResult",
    "make_node_id",
    "make_module_qualname",
    "build_embedding_text",
]


def init_hooks() -> None:
    """Initialize codegraph hooks. Called lazily when hooks are needed."""
    try:
        import agentnexus.codegraph.hooks  # noqa: F401
    except Exception:
        pass

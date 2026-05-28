"""Code knowledge graph data models.

Pure data structures for nodes, edges, and parse results.
No external dependencies beyond the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeKind(str, Enum):
    """Code entity types."""

    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"


class EdgeKind(str, Enum):
    """Relationship types between code entities."""

    CONTAINS = "contains"  # file contains class/function/method
    INHERITS = "inherits"  # class inherits class
    CALLS = "calls"  # function/method calls function/method
    IMPORTS = "imports"  # module imports symbol
    USES = "uses"  # function uses variable
    DECORATES = "decorates"  # decorator decorates entity


@dataclass
class NodeData:
    """Parsed node data before persistence."""

    id: str  # e.g. "function:pkg.module.func"
    kind: str  # NodeKind value
    name: str  # short name, e.g. "file_write"
    qualified_name: str  # e.g. "agentnexus.tools.file_ops.file_write"
    file_path: str  # relative to project root
    language: str  # e.g. "python"
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0
    docstring: str | None = None
    signature: str | None = None
    visibility: str | None = None  # "public" / "private" / "protected"
    is_exported: bool = False
    is_async: bool = False
    is_static: bool = False
    is_abstract: bool = False
    decorators: list[str] = field(default_factory=list)
    type_parameters: list[str] = field(default_factory=list)


@dataclass
class EdgeData:
    """Parsed edge data before persistence."""

    source: str  # source node id
    target: str  # target node id
    kind: str  # EdgeKind value
    metadata: dict | None = None
    line: int | None = None
    col: int | None = None


@dataclass
class ParseResult:
    """Result of parsing a single file."""

    nodes: list[NodeData] = field(default_factory=list)
    edges: list[EdgeData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    partial: bool = False  # True means parse failed, keep old nodes


def make_node_id(kind: str, qualified_name: str, file_path: str | None = None) -> str:
    """Generate a node ID from kind and qualified name.

    For file nodes, use the file path as the identifier.
    """
    kind_str = kind.value if isinstance(kind, Enum) else kind
    if kind_str == NodeKind.FILE:
        return f"file:{file_path}"
    return f"{kind_str}:{qualified_name}"


def make_module_qualname(file_path: str, project_root: str = "") -> str:
    """Convert a file path to a Python module qualified name.

    Examples:
        agentnexus/tools/file_ops.py -> agentnexus.tools.file_ops
        tests/test_foo.py -> tests.test_foo
    """
    rel = file_path
    if project_root and rel.startswith(project_root):
        rel = rel[len(project_root) :].lstrip("/").lstrip("\\")

    # Remove extension and convert path separators
    parts = rel.replace("\\", "/").split("/")
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # Remove __init__ from qualified name
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# Embedding text generation thresholds
EMBEDDING_MAX_CHARS = 2048  # ~512 tokens
DOCSTRING_PREVIEW_MAX_CHARS = 200


def build_embedding_text(node: NodeData) -> str:
    """Generate embedding text for a code entity.

    For functions/methods: name + module + signature + docstring preview.
    For classes: name + module + bases + docstring preview.
    Returns empty string for variable/import (no embedding).
    """
    if node.kind in (NodeKind.VARIABLE, NodeKind.IMPORT):
        return ""

    parts: list[str] = []

    if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
        parts.append(node.name)
        # Module part of qualified name
        module = node.qualified_name.rsplit(".", 1)[0] if "." in node.qualified_name else ""
        if module:
            parts.append(module)
        if node.signature:
            parts.append(node.signature)
        if node.docstring:
            first_para = node.docstring.split("\n\n")[0][:DOCSTRING_PREVIEW_MAX_CHARS]
            parts.append(first_para)

    elif node.kind == NodeKind.CLASS:
        parts.append(node.name)
        module = node.qualified_name.rsplit(".", 1)[0] if "." in node.qualified_name else ""
        if module:
            parts.append(module)
        if node.decorators:
            parts.append(f"decorators: {', '.join(node.decorators)}")
        if node.docstring:
            first_para = node.docstring.split("\n\n")[0][:DOCSTRING_PREVIEW_MAX_CHARS]
            parts.append(first_para)

    text = " ".join(filter(None, parts))
    return text[:EMBEDDING_MAX_CHARS]


__all__ = [
    "NodeKind",
    "EdgeKind",
    "NodeData",
    "EdgeData",
    "ParseResult",
    "make_node_id",
    "make_module_qualname",
    "build_embedding_text",
    "EMBEDDING_MAX_CHARS",
]

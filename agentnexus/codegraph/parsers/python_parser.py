"""Python AST-based code parser.

Extracts classes, functions, methods, imports, and their relationships
from Python source files using the built-in ast module.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentnexus.codegraph.models import (
    EdgeData,
    EdgeKind,
    NodeData,
    NodeKind,
    ParseResult,
    make_module_qualname,
    make_node_id,
)

# Decorators that indicate abstract methods
_ABSTRACT_DECORATORS = {"abstractmethod", "abc.abstractmethod"}


class PythonParser:
    """Python AST parser implementing the LanguageParser protocol."""

    @property
    def language(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> list[str]:
        return [".py"]

    def parse_file(self, file_path: Path, content: str) -> ParseResult:
        """Parse a Python file and return nodes + edges."""
        try:
            tree = ast.parse(content, filename=str(file_path))
        except SyntaxError as e:
            return ParseResult(
                nodes=[],
                edges=[],
                errors=[f"SyntaxError at line {e.lineno}: {e.msg}"],
                partial=True,
            )

        visitor = _CodeGraphVisitor(file_path)
        visitor.visit(tree)
        return ParseResult(
            nodes=visitor.nodes,
            edges=visitor.edges,
            errors=visitor.errors,
            partial=False,
        )


class _CodeGraphVisitor(ast.NodeVisitor):
    """AST visitor that extracts code structure for the knowledge graph."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._file_str = str(file_path).replace("\\", "/")
        self._module_qualname = make_module_qualname(self._file_str)
        self._language = "python"
        self.nodes: list[NodeData] = []
        self.edges: list[EdgeData] = []
        self.errors: list[str] = []
        self._current_class: str | None = None
        self._current_class_qualname: str | None = None
        self._current_function: str | None = None
        self._current_function_qualname: str | None = None

        # Add file node
        self.nodes.append(NodeData(
            id=make_node_id(NodeKind.FILE, "", self._file_str),
            kind=NodeKind.FILE,
            name=file_path.name,
            qualified_name=self._module_qualname,
            file_path=self._file_str,
            language=self._language,
            start_line=1,
            end_line=content.count("\n") + 1 if (content := "") else 0,
        ))

    def _get_qualname(self, name: str) -> str:
        """Build qualified name based on current context."""
        parts = [self._module_qualname]
        if self._current_class:
            parts.append(self._current_class)
        if self._current_function and not self._current_class:
            parts.append(self._current_function)
        parts.append(name)
        return ".".join(parts)

    def _get_decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
        """Extract decorator names."""
        decorators: list[str] = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(ast.dump(dec))
            elif isinstance(dec, ast.Call):
                if isinstance(dec.func, ast.Name):
                    decorators.append(dec.func.id)
                elif isinstance(dec.func, ast.Attribute):
                    decorators.append(ast.dump(dec.func))
        return decorators

    def _get_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        """Build function signature string."""
        args = node.args
        parts: list[str] = []

        # Positional args
        defaults_offset = len(args.args) - len(args.defaults)
        for i, arg in enumerate(args.args):
            if arg.arg == "self" or arg.arg == "cls":
                continue
            sig = arg.arg
            if arg.annotation:
                sig += f": {ast.unparse(arg.annotation)}"
            default_idx = i - defaults_offset
            if default_idx >= 0:
                sig += f" = {ast.unparse(args.defaults[default_idx])}"
            parts.append(sig)

        # *args
        if args.vararg:
            sig = f"*{args.vararg.arg}"
            if args.vararg.annotation:
                sig += f": {ast.unparse(args.vararg.annotation)}"
            parts.append(sig)

        # Keyword-only args
        for i, arg in enumerate(args.kwonlyargs):
            sig = arg.arg
            if arg.annotation:
                sig += f": {ast.unparse(arg.annotation)}"
            if args.kw_defaults[i] is not None:
                sig += f" = {ast.unparse(args.kw_defaults[i])}"
            parts.append(sig)

        # **kwargs
        if args.kwarg:
            sig = f"**{args.kwarg.arg}"
            if args.kwarg.annotation:
                sig += f": {ast.unparse(args.kwarg.annotation)}"
            parts.append(sig)

        # Return annotation
        ret = ""
        if node.returns:
            ret = f" -> {ast.unparse(node.returns)}"

        return f"({', '.join(parts)}){ret}"

    def _get_visibility(self, name: str) -> str:
        """Determine visibility from naming convention."""
        if name.startswith("__") and name.endswith("__"):
            return "public"  # dunder methods are public
        if name.startswith("_"):
            return "private"
        return "public"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Process class definition."""
        qualname = self._get_qualname(node.name)
        node_id = make_node_id(NodeKind.CLASS, qualname)
        decorators = self._get_decorators(node)
        docstring = ast.get_docstring(node)

        # Extract base classes
        bases: list[str] = []
        for base in node.bases:
            bases.append(ast.unparse(base))

        self.nodes.append(NodeData(
            id=node_id,
            kind=NodeKind.CLASS,
            name=node.name,
            qualified_name=qualname,
            file_path=self._file_str,
            language=self._language,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            start_column=node.col_offset,
            end_column=node.end_col_offset or 0,
            docstring=docstring,
            decorators=decorators,
        ))

        # contains edge from file
        self.edges.append(EdgeData(
            source=make_node_id(NodeKind.FILE, "", self._file_str),
            target=node_id,
            kind=EdgeKind.CONTAINS,
            line=node.lineno,
        ))

        # inherits edges
        for base in bases:
            # Try to resolve to a qualified name in the same module
            base_id = make_node_id(NodeKind.CLASS, base)
            self.edges.append(EdgeData(
                source=node_id,
                target=base_id,
                kind=EdgeKind.INHERITS,
                line=node.lineno,
            ))

        # Process class body
        prev_class = self._current_class
        prev_class_qualname = self._current_class_qualname
        self._current_class = node.name
        self._current_class_qualname = qualname
        self.generic_visit(node)
        self._current_class = prev_class
        self._current_class_qualname = prev_class_qualname

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Process function/method definition."""
        self._process_func(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Process async function/method definition."""
        self._process_func(node, is_async=True)

    def _process_func(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_async: bool,
    ) -> None:
        """Process a function or method definition."""
        is_method = self._current_class is not None
        kind = NodeKind.METHOD if is_method else NodeKind.FUNCTION

        qualname = self._get_qualname(node.name)
        node_id = make_node_id(kind, qualname)
        decorators = self._get_decorators(node)
        docstring = ast.get_docstring(node)
        signature = self._get_signature(node)
        visibility = self._get_visibility(node.name)
        is_abstract = any(d in _ABSTRACT_DECORATORS for d in decorators)
        is_static = "staticmethod" in decorators

        self.nodes.append(NodeData(
            id=node_id,
            kind=kind,
            name=node.name,
            qualified_name=qualname,
            file_path=self._file_str,
            language=self._language,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            start_column=node.col_offset,
            end_column=node.end_col_offset or 0,
            docstring=docstring,
            signature=signature,
            visibility=visibility,
            is_async=is_async,
            is_static=is_static,
            is_abstract=is_abstract,
            decorators=decorators,
        ))

        # contains edge from file
        self.edges.append(EdgeData(
            source=make_node_id(NodeKind.FILE, "", self._file_str),
            target=node_id,
            kind=EdgeKind.CONTAINS,
            line=node.lineno,
        ))

        # contains edge from class (for methods)
        if is_method and self._current_class_qualname:
            class_id = make_node_id(NodeKind.CLASS, self._current_class_qualname)
            self.edges.append(EdgeData(
                source=class_id,
                target=node_id,
                kind=EdgeKind.CONTAINS,
                line=node.lineno,
            ))

        # Process function body for calls and variable usage
        prev_func = self._current_function
        prev_func_qualname = self._current_function_qualname
        self._current_function = node.name
        self._current_function_qualname = qualname
        self._scan_body(node.body, node_id)
        self._current_function = prev_func
        self._current_function_qualname = prev_func_qualname

    def _scan_body(self, stmts: list[ast.stmt], owner_id: str) -> None:
        """Scan function/method body for calls and variable usage."""
        for stmt in ast.walk(ast.Module(body=stmts, type_ignores=[])):
            if isinstance(stmt, ast.Call):
                self._record_call(stmt, owner_id)

    def _record_call(self, call: ast.Call, caller_id: str) -> None:
        """Record a call edge from caller to callee."""
        callee_name = _resolve_call_name(call.func)
        if not callee_name:
            return

        # Try to resolve within the same module
        # First check if it's a method call on self
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
            if call.func.value.id == "self" and self._current_class_qualname:
                callee_qualname = f"{self._current_class_qualname}.{call.func.attr}"
                callee_id = make_node_id(NodeKind.METHOD, callee_qualname)
                self.edges.append(EdgeData(
                    source=caller_id,
                    target=callee_id,
                    kind=EdgeKind.CALLS,
                    line=call.lineno,
                ))
                return

        # For other calls, create an unqualified edge
        # The full resolution happens at the store level
        callee_id = make_node_id(NodeKind.FUNCTION, callee_name)
        self.edges.append(EdgeData(
            source=caller_id,
            target=callee_id,
            kind=EdgeKind.CALLS,
            line=call.lineno,
        ))

    def visit_Import(self, node: ast.Import) -> None:
        """Process import statements."""
        for alias in node.names:
            name = alias.asname or alias.name
            import_id = make_node_id(NodeKind.IMPORT, f"{self._module_qualname}.{name}")
            self.nodes.append(NodeData(
                id=import_id,
                kind=NodeKind.IMPORT,
                name=name,
                qualified_name=f"{self._module_qualname}.{name}",
                file_path=self._file_str,
                language=self._language,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                start_column=node.col_offset,
                end_column=node.end_col_offset or 0,
            ))

            # imports edge from file
            self.edges.append(EdgeData(
                source=make_node_id(NodeKind.FILE, "", self._file_str),
                target=import_id,
                kind=EdgeKind.IMPORTS,
                line=node.lineno,
                metadata={"module": alias.name, "alias": alias.asname},
            ))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Process from...import statements."""
        module = node.module or ""
        for alias in node.names:
            name = alias.asname or alias.name
            import_id = make_node_id(NodeKind.IMPORT, f"{self._module_qualname}.{name}")
            self.nodes.append(NodeData(
                id=import_id,
                kind=NodeKind.IMPORT,
                name=name,
                qualified_name=f"{self._module_qualname}.{name}",
                file_path=self._file_str,
                language=self._language,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                start_column=node.col_offset,
                end_column=node.end_col_offset or 0,
            ))

            # imports edge from file
            self.edges.append(EdgeData(
                source=make_node_id(NodeKind.FILE, "", self._file_str),
                target=import_id,
                kind=EdgeKind.IMPORTS,
                line=node.lineno,
                metadata={
                    "module": module,
                    "name": alias.name,
                    "alias": alias.asname,
                    "is_from_import": True,
                },
            ))


def _resolve_call_name(node: ast.expr) -> str | None:
    """Resolve a call expression to a string name."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _resolve_call_name(node.func)
    return None


__all__ = ["PythonParser"]

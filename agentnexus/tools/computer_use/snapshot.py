"""Accessibility tree → YAML formatter for desktop elements.

Converts a DesktopElement tree into the same YAML format used by
browser aria_snapshot, so the LLM sees a consistent structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentnexus.tools.computer_use.element import DesktopElement

# ---------------------------------------------------------------------------
# Role sets (shared with element.py for is_interactive())
# ---------------------------------------------------------------------------

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox",
    "checkbox", "radio", "switch", "slider", "spinbutton",
    "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "option", "scrollbar", "tablist",
    "dialog", "alertdialog", "splitbutton",
}

READING_ROLES = INTERACTIVE_ROLES | {
    "heading", "text", "status", "alert", "log",
    "marquee", "timer", "note", "definition",
    "progressbar",
}

# ---------------------------------------------------------------------------
# YAML formatting
# ---------------------------------------------------------------------------


def format_desktop_yaml(
    elements: list[DesktopElement],
    max_nodes: int = 100,
) -> str:
    """Format a list of DesktopElement trees into YAML-like text.

    Output matches browser aria_snapshot style:
        - window "Notepad":
          - menubar "":
            - menuitem "File":
          - group "":
            - textbox "Text area" [value="Hello"] [focused]:

    Args:
        elements: Root desktop elements (typically one per window).
        max_nodes: Maximum total nodes to include (priority truncation).

    Returns:
        YAML-formatted string for LLM consumption.
    """
    if not elements:
        return "(empty desktop)"

    # Flatten all nodes for truncation
    flat: list[DesktopElement] = []
    for elem in elements:
        _flatten(elem, flat)

    # Truncate by priority
    if len(flat) > max_nodes:
        flat = _truncate_by_priority(flat, max_nodes)

    # Format
    lines: list[str] = []
    for elem in elements:
        _format_tree(elem, lines, depth=0, flat_set=set(id(n) for n in flat))

    return "\n".join(lines)


def format_desktop_numbered(
    elements: list[DesktopElement],
    max_nodes: int = 100,
    start_idx: int = 1,
) -> str:
    """Format desktop elements as numbered text for LLM consumption.

    Mirrors browser _format_a11y_tree output:
        [1] window "Notepad"
        [2] menubar ""
        [3] menuitem "File"
        [4] textbox "Text area" [focused]

    Args:
        elements: Root desktop elements.
        max_nodes: Maximum nodes to include.
        start_idx: Starting index number.

    Returns:
        Numbered text for LLM element reference.
    """
    if not elements:
        return "(no elements)"

    # Flatten all nodes
    flat: list[DesktopElement] = []
    for elem in elements:
        _flatten(elem, flat)

    # Truncate by priority
    if len(flat) > max_nodes:
        flat = _truncate_by_priority(flat, max_nodes)

    # Format numbered lines
    lines: list[str] = []
    idx = start_idx
    for elem in flat:
        line = _format_element_line(elem, idx)
        lines.append(line)
        idx += 1

    return "\n".join(lines)


def _flatten(elem: DesktopElement, out: list[DesktopElement]) -> None:
    """Recursively flatten a DesktopElement tree into a list."""
    out.append(elem)
    for child in elem.children:
        _flatten(child, out)


def _truncate_by_priority(
    elements: list[DesktopElement],
    max_nodes: int,
) -> list[DesktopElement]:
    """Truncate by priority: interactive > reading > other.

    Same logic as browser _truncate_by_priority.
    """
    if len(elements) <= max_nodes:
        return elements

    bucket_interactive = []
    bucket_reading = []
    bucket_other = []

    for elem in elements:
        if elem.role in INTERACTIVE_ROLES:
            bucket_interactive.append(elem)
        elif elem.role in READING_ROLES:
            bucket_reading.append(elem)
        else:
            bucket_other.append(elem)

    result: list[DesktopElement] = []
    remaining = max_nodes
    for bucket in [bucket_interactive, bucket_reading, bucket_other]:
        if remaining <= 0:
            break
        take = bucket[:remaining]
        result.extend(take)
        remaining -= len(take)

    return result


def _format_tree(
    elem: DesktopElement,
    lines: list[str],
    depth: int,
    flat_set: set[int],
) -> None:
    """Recursively format an element tree in YAML style."""
    if id(elem) not in flat_set:
        return

    indent = "  " * depth
    role = elem.role
    name = elem.name

    # Build annotation parts
    annotations: list[str] = []
    if elem.value is not None:
        annotations.append(f'[value="{elem.value}"]')
    if elem.focused:
        annotations.append("[focused]")
    if elem.checked is True:
        annotations.append("[checked]")
    elif elem.checked is False:
        annotations.append("[unchecked]")
    if not elem.enabled:
        annotations.append("[disabled]")

    ann_str = " ".join(annotations)

    if name:
        line = f'{indent}- {role} "{name}"'
    else:
        line = f"{indent}- {role} \"\""

    if ann_str:
        line += f" {ann_str}"

    if elem.children:
        line += ":"

    lines.append(line)

    for child in elem.children:
        _format_tree(child, lines, depth + 1, flat_set)


def _format_element_line(elem: DesktopElement, idx: int) -> str:
    """Format a single element as a numbered line.

    Format:
        [1] button "OK"
        [2] textbox "Search" [value="hello"] [focused]
        [3] checkbox "Remember me" [checked]
    """
    role = elem.role
    name = elem.name

    parts = [f"[{idx}]", role]
    if name:
        parts.append(f'"{name}"')

    # Add bounds for unnamed elements (agent needs them for pos parameter)
    if not name and elem.bounds != (0, 0, 0, 0):
        x, y, w, h = elem.bounds
        parts.append(f"[box={x},{y},{w},{h}]")

    # State annotations
    if elem.value is not None:
        parts.append(f'[value="{elem.value}"]')
    if elem.focused:
        parts.append("[focused]")
    if elem.checked is True:
        parts.append("[checked]")
    elif elem.checked is False:
        parts.append("[unchecked]")
    if not elem.enabled:
        parts.append("[disabled]")

    return " ".join(parts)

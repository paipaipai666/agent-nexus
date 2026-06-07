"""Unified desktop element model and cross-platform role mapping.

Maps platform-specific accessibility roles (Windows UIA, Linux AT-SPI, macOS AX)
to a standardized set of role strings that match browser aria_snapshot conventions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Cross-platform role mapping
# ---------------------------------------------------------------------------

_WINDOWS_ROLE_MAP: dict[str, str] = {
    "Button": "button",
    "Edit": "textbox",
    "Text": "text",
    "Hyperlink": "link",
    "CheckBox": "checkbox",
    "RadioButton": "radio",
    "ComboBox": "combobox",
    "ListItem": "listitem",
    "TreeItem": "treeitem",
    "TabItem": "tab",
    "MenuItem": "menuitem",
    "Slider": "slider",
    "Window": "window",
    "Pane": "group",
    "ToolBar": "toolbar",
    "MenuBar": "menubar",
    "ScrollBar": "scrollbar",
    "StatusBar": "status",
    "Header": "heading",
    "Table": "table",
    "DataGrid": "datagrid",
    "Image": "img",
    "Document": "document",
    "Calendar": "calendar",
    "ProgressBar": "progressbar",
    "Spinner": "spinbutton",
    "SplitButton": "splitbutton",
    "Group": "group",
    "Tab": "tablist",
    "List": "list",
    "Tree": "tree",
    "Menu": "menu",
    "Separator": "separator",
    "Custom": "generic",
}

_LINUX_ROLE_MAP: dict[str, str] = {
    "push_button": "button",
    "text": "textbox",
    "filler": "group",
    "menu_item": "menuitem",
    "toggle_button": "switch",
    "check_box": "checkbox",
    "radio_button": "radio",
    "combo_box": "combobox",
    "list_item": "listitem",
    "tree_item": "treeitem",
    "page_tab": "tab",
    "slider": "slider",
    "frame": "window",
    "panel": "group",
    "tool_bar": "toolbar",
    "menu_bar": "menubar",
    "scroll_bar": "scrollbar",
    "status_bar": "status",
    "heading": "heading",
    "table": "table",
    "table_cell": "cell",
    "image": "img",
    "document": "document",
    "progress_bar": "progressbar",
    "spin_button": "spinbutton",
    "menu": "menu",
    "separator": "separator",
    "static_text": "text",
    "entry": "textbox",
    "label": "text",
    "link": "link",
    "tree": "tree",
    "list": "list",
    "page_tab_list": "tablist",
    "window": "window",
    "dialog": "dialog",
    "alert": "alert",
    "unknown": "generic",
}

_MACOS_ROLE_MAP: dict[str, str] = {
    "AXButton": "button",
    "AXTextField": "textbox",
    "AXTextArea": "textbox",
    "AXStaticText": "text",
    "AXLink": "link",
    "AXCheckBox": "checkbox",
    "AXRadioButton": "radio",
    "AXPopUpButton": "combobox",
    "AXComboBox": "combobox",
    "AXRow": "listitem",
    "AXOutline": "tree",
    "AXTab": "tab",
    "AXMenuItem": "menuitem",
    "AXSlider": "slider",
    "AXWindow": "window",
    "AXGroup": "group",
    "AXToolbar": "toolbar",
    "AXMenuBar": "menubar",
    "AXScrollArea": "scrollbar",
    "AXScrollBar": "scrollbar",
    "AXTable": "table",
    "AXCell": "cell",
    "AXImage": "img",
    "AXDocument": "document",
    "AXProgressIndicator": "progressbar",
    "AXIncrementor": "spinbutton",
    "AXMenu": "menu",
    "AXSeparator": "separator",
    "AXList": "list",
    "AXTabGroup": "tablist",
    "AXSplitGroup": "splitter",
    "AXSplitter": "splitter",
    "AXDialog": "dialog",
    "AXSheet": "dialog",
    "AXAlert": "alert",
    "AXHeading": "heading",
    "AXGeneric": "generic",
}

# Reverse maps for lookup by platform
ROLE_MAPS: dict[str, dict[str, str]] = {
    "windows": _WINDOWS_ROLE_MAP,
    "linux": _LINUX_ROLE_MAP,
    "macos": _MACOS_ROLE_MAP,
}


def normalize_role(platform: str, platform_role: str) -> str:
    """Map a platform-specific role to a unified role string.

    Args:
        platform: One of 'windows', 'linux', 'macos'.
        platform_role: The platform-specific role name.

    Returns:
        Unified role string, or 'generic' if unmapped.
    """
    role_map = ROLE_MAPS.get(platform, {})
    return role_map.get(platform_role, "generic")


# ---------------------------------------------------------------------------
# DesktopElement — immutable unified element model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesktopElement:
    """Unified representation of a desktop UI element across platforms.

    Mirrors the browser aria_snapshot node structure so the LLM sees
    a consistent YAML format regardless of platform.
    """

    role: str
    """Unified role name (e.g. 'button', 'textbox', 'window')."""

    name: str
    """Element name / accessible label / text content."""

    value: str | None = None
    """Current value (for text inputs, sliders, etc.)."""

    enabled: bool = True
    """Whether the element is enabled for interaction."""

    focused: bool = False
    """Whether the element currently has keyboard focus."""

    checked: bool | None = None
    """For checkbox/radio: True=checked, False=unchecked, None=N/A."""

    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    """Bounding rectangle (x, y, width, height) in screen coordinates."""

    children: tuple[DesktopElement, ...] = ()
    """Child elements in the accessibility tree."""

    platform_role: str = ""
    """Original platform-specific role name (for debugging)."""

    platform_id: str = ""
    """Platform-specific identifier (AutomationId, path, etc.)."""

    @property
    def x(self) -> int:
        return self.bounds[0]

    @property
    def y(self) -> int:
        return self.bounds[1]

    @property
    def width(self) -> int:
        return self.bounds[2]

    @property
    def height(self) -> int:
        return self.bounds[3]

    def is_interactive(self) -> bool:
        """Check if this element has an interactive role."""
        from agentnexus.tools.computer_use.snapshot import INTERACTIVE_ROLES
        return self.role in INTERACTIVE_ROLES

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON/diagnostic use."""
        return {
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "enabled": self.enabled,
            "focused": self.focused,
            "checked": self.checked,
            "bounds": list(self.bounds),
            "platform_role": self.platform_role,
            "platform_id": self.platform_id,
            "children": [c.to_dict() for c in self.children],
        }

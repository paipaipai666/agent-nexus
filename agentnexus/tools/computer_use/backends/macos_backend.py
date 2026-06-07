"""macOS accessibility backend using pyobjc ApplicationServices (AX).

Requires: pip install pyobjc-framework-ApplicationServices

The user must grant Accessibility permission to the terminal/app
running AgentNexus (System Settings → Privacy & Security → Accessibility).
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from agentnexus.tools.computer_use.backends.base import ComputerUseBackend
from agentnexus.tools.computer_use.element import DesktopElement, normalize_role

logger = logging.getLogger(__name__)

# Lazy import guard
_ax: Any = None
_cf: Any = None


def _ensure_ax() -> tuple[Any, Any]:
    """Import ApplicationServices and CoreFoundation on demand."""
    global _ax, _cf
    if _ax is None:
        try:
            import ApplicationServices as ax_mod
            import CoreFoundation as cf_mod
            _ax = ax_mod
            _cf = cf_mod
        except ImportError as e:
            raise RuntimeError(
                "pyobjc-framework-ApplicationServices 未安装。"
                "请执行: pip install pyobjc-framework-ApplicationServices"
            ) from e
    return _ax, _cf


def _check_accessibility() -> bool:
    """Check if the process has accessibility permissions."""
    ax, _ = _ensure_ax()
    try:
        return ax.AXIsProcessTrusted()
    except Exception:
        return False


class MacOSBackend(ComputerUseBackend):
    """macOS AX backend via pyobjc ApplicationServices."""

    async def list_windows(self) -> list[dict[str, Any]]:
        """List all visible top-level windows."""
        ax, cf = _ensure_ax()
        if not _check_accessibility():
            return [{"error": "未授权辅助功能权限。请在 系统设置 → 隐私与安全性 → 辅助功能 中授权。"}]

        system_wide = ax.AXUIElementCreateSystemWide()
        windows: list[dict[str, Any]] = []

        # Get running applications
        workspace_class = _get_ns_workspace()
        if workspace_class:
            apps = workspace_class.sharedWorkspace().runningApplications()
            for app in apps:
                if app.activationPolicy() == 0:  # NSApplicationActivationPolicyRegular
                    pid = app.processIdentifier()
                    app_elem = ax.AXUIElementCreateApplication(pid)
                    app_name = app.localizedName() or ""
                    win_list = _get_attr(app_elem, "AXWindows") or []
                    for win in win_list:
                        title = _get_attr(win, "AXTitle") or ""
                        bounds = _get_bounds(win)
                        windows.append({
                            "title": title,
                            "app_name": app_name,
                            "bounds": bounds,
                            "pid": pid,
                        })

        return windows

    async def get_snapshot(
        self,
        app_name: str | None = None,
        window_title: str | None = None,
    ) -> list[DesktopElement]:
        """Get accessibility tree for matching windows."""
        ax, _ = _ensure_ax()
        if not _check_accessibility():
            raise RuntimeError("未授权辅助功能权限。请在 系统设置 → 隐私与安全性 → 辅助功能 中授权。")

        roots: list[DesktopElement] = []
        workspace_class = _get_ns_workspace()
        if not workspace_class:
            return roots

        apps = workspace_class.sharedWorkspace().runningApplications()
        for app in apps:
            if app.activationPolicy() != 0:
                continue

            app_name_str = app.localizedName() or ""
            if app_name and app_name.lower() not in app_name_str.lower():
                continue

            pid = app.processIdentifier()
            app_elem = ax.AXUIElementCreateApplication(pid)
            win_list = _get_attr(app_elem, "AXWindows") or []

            for win in win_list:
                title = _get_attr(win, "AXTitle") or ""
                if window_title and window_title.lower() not in title.lower():
                    continue

                elem = self._build_element(win, pid)
                if elem:
                    roots.append(elem)

        return roots

    def _build_element(
        self,
        ax_elem: Any,
        pid: int,
        depth: int = 0,
        path: str = "",
    ) -> DesktopElement | None:
        """Recursively build a DesktopElement from an AXUIElement."""
        if depth > 30:
            return None

        ax, _ = _ensure_ax()

        try:
            role = _get_attr(ax_elem, "AXRole") or "AXUnknown"
            name = _get_attr(ax_elem, "AXTitle") or _get_attr(ax_elem, "AXDescription") or ""
            value = _get_attr(ax_elem, "AXValue")
            enabled = _get_attr(ax_elem, "AXEnabled")
            if enabled is None:
                enabled = True

            focused = bool(_get_attr(ax_elem, "AXFocused"))
            bounds = _get_bounds(ax_elem)

            # Check state for checkboxes/radios
            checked = None
            if role in ("AXCheckBox", "AXRadioButton"):
                val = _get_attr(ax_elem, "AXValue")
                checked = bool(val) if val is not None else None

            current_path = f"{path}/{role}"

            # Build children
            children: list[DesktopElement] = []
            child_elems = _get_attr(ax_elem, "AXChildren") or []
            for i, child in enumerate(child_elems):
                child_path = f"{current_path}[{i}]"
                child_elem = self._build_element(child, pid, depth + 1, child_path)
                if child_elem:
                    children.append(child_elem)

            return DesktopElement(
                role=normalize_role("macos", role),
                name=str(name) if name else "",
                value=str(value) if value is not None else None,
                enabled=bool(enabled),
                focused=focused,
                checked=checked,
                bounds=bounds,
                children=tuple(children),
                platform_role=role,
                platform_id=current_path,
            )
        except Exception as e:
            logger.debug("Failed to build element: %s", e)
            return None

    async def click(
        self,
        element_id: str,
        button: str = "left",
        clicks: int = 1,
    ) -> None:
        """Click an element."""
        ax, _ = _ensure_ax()
        elem = self._find_element(element_id)
        if button == "right":
            # AX doesn't have native right-click; use cliclick
            bounds = _get_bounds(elem)
            x = bounds[0] + bounds[2] // 2
            y = bounds[1] + bounds[3] // 2
            subprocess.run(["cliclick", f"c:{x},{y}", f"rc:{x},{y}"], check=False)
        else:
            for _ in range(clicks):
                ax.AXUIElementPerformAction(elem, "AXPress")

    async def type_text(
        self,
        element_id: str,
        text: str,
        clear: bool = True,
    ) -> None:
        """Type text into an element."""
        ax, _ = _ensure_ax()
        elem = self._find_element(element_id)

        # Focus the element
        ax.AXUIElementSetAttributeValue(elem, "AXFocused", True)

        if clear:
            # Select all and delete
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to keystroke "a" using command down'],
                check=False,
            )
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to key code 51'],
                check=False,
            )

        # Type text via osascript
        subprocess.run(
            ["osascript", "-e", f'tell application "System Events" to keystroke "{text}"'],
            check=False,
        )

    async def press_key(self, keys: str) -> None:
        """Press a key combination."""
        # Convert to osascript format
        parts = keys.lower().split("+")
        key = parts[-1]
        modifiers = parts[:-1]

        mod_str = ""
        if "command" in modifiers or "cmd" in modifiers:
            mod_str += "command down, "
        if "option" in modifiers or "alt" in modifiers:
            mod_str += "option down, "
        if "control" in modifiers or "ctrl" in modifiers:
            mod_str += "control down, "
        if "shift" in modifiers:
            mod_str += "shift down, "

        if mod_str:
            mod_str = mod_str.rstrip(", ")
            script = f'tell application "System Events" to keystroke "{key}" using {{{mod_str}}}'
        else:
            script = f'tell application "System Events" to keystroke "{key}"'

        subprocess.run(["osascript", "-e", script], check=False)

    async def scroll(
        self,
        element_id: str | None,
        direction: str = "down",
        amount: int = 3,
    ) -> None:
        """Scroll an element or the screen."""
        if element_id:
            ax, _ = _ensure_ax()
            elem = self._find_element(element_id)
            # AX scroll is platform-specific; use cliclick as fallback
            bounds = _get_bounds(elem)
            x = bounds[0] + bounds[2] // 2
            y = bounds[1] + bounds[3] // 2
            if direction == "down":
                subprocess.run(["cliclick", f"sc:{x},{y},-{amount * 3}"], check=False)
            elif direction == "up":
                subprocess.run(["cliclick", f"sc:{x},{y},{amount * 3}"], check=False)
        else:
            # Screen scroll
            if direction == "down":
                subprocess.run(["cliclick", f"sc:0,0,-{amount * 3}"], check=False)
            elif direction == "up":
                subprocess.run(["cliclick", f"sc:0,0,{amount * 3}"], check=False)

    async def select(
        self,
        element_id: str,
        value: str,
    ) -> None:
        """Select a value in a combobox."""
        ax, _ = _ensure_ax()
        elem = self._find_element(element_id)
        # Try AXPress to open, then find menu item
        ax.AXUIElementPerformAction(elem, "AXPress")

        # Search for the value in children
        children = _get_attr(elem, "AXChildren") or []
        for child in children:
            child_name = _get_attr(child, "AXTitle") or ""
            if value.lower() in child_name.lower():
                ax.AXUIElementPerformAction(child, "AXPress")
                return
        raise ValueError(f"找不到选项: {value}")

    async def toggle(
        self,
        element_id: str,
        checked: bool | None = None,
    ) -> None:
        """Toggle a checkbox."""
        ax, _ = _ensure_ax()
        elem = self._find_element(element_id)
        if checked is not None:
            current = bool(_get_attr(elem, "AXValue"))
            if current != checked:
                ax.AXUIElementPerformAction(elem, "AXPress")
        else:
            ax.AXUIElementPerformAction(elem, "AXPress")

    async def launch_app(
        self,
        app_path: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Launch an application."""
        cmd = ["open", "-a", app_path] + (args or [])
        proc = subprocess.Popen(cmd)
        return {
            "pid": proc.pid,
            "title": "",
            "app_name": app_path,
        }

    async def get_clipboard(self) -> str:
        """Read clipboard content."""
        try:
            import pyperclip
            return pyperclip.paste()
        except ImportError:
            result = subprocess.run(
                ["pbpaste"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout

    async def set_clipboard(self, text: str) -> None:
        """Write to clipboard."""
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            subprocess.run(["pbcopy"], input=text.encode(), check=False)

    def _find_element(self, element_id: str) -> Any:
        """Find an AX element by path."""
        ax, _ = _ensure_ax()
        workspace_class = _get_ns_workspace()
        if not workspace_class:
            raise ValueError("无法访问 NSWorkspace")

        apps = workspace_class.sharedWorkspace().runningApplications()
        for app in apps:
            if app.activationPolicy() != 0:
                continue
            pid = app.processIdentifier()
            app_elem = ax.AXUIElementCreateApplication(pid)
            result = self._search_element(app_elem, element_id, pid, "")
            if result:
                return result

        raise ValueError(f"找不到元素: {element_id}")

    def _search_element(
        self,
        ax_elem: Any,
        target: str,
        pid: int,
        path: str,
    ) -> Any:
        """Recursively search for an element by platform_id (path)."""
        role = _get_attr(ax_elem, "AXRole") or "AXUnknown"
        current_path = f"{path}/{role}"

        if current_path == target:
            return ax_elem

        children = _get_attr(ax_elem, "AXChildren") or []
        for i, child in enumerate(children):
            child_path = f"{current_path}[{i}]"
            result = self._search_element(child, target, pid, child_path)
            if result:
                return result

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_attr(elem: Any, attr: str) -> Any:
    """Get an AX attribute value, returning None on error."""
    ax, _ = _ensure_ax()
    err, value = ax.AXUIElementCopyAttributeValue(elem, attr, None)
    if err == 0:
        return value
    return None


def _get_bounds(elem: Any) -> tuple[int, int, int, int]:
    """Get the bounding rectangle of an AX element."""
    ax, cf = _ensure_ax()
    pos = _get_attr(elem, "AXPosition")
    size = _get_attr(elem, "AXSize")
    if pos and size:
        x, y = _cf_point(pos)
        w, h = _cf_size(size)
        return (x, y, w, h)
    return (0, 0, 0, 0)


def _cf_point(point: Any) -> tuple[int, int]:
    """Extract x, y from a CGPoint/CFDictionary."""
    try:
        x = point.get("X", 0)
        y = point.get("Y", 0)
        return (int(x), int(y))
    except Exception:
        return (0, 0)


def _cf_size(size: Any) -> tuple[int, int]:
    """Extract w, h from a CGSize/CFDictionary."""
    try:
        w = size.get("Width", 0)
        h = size.get("Height", 0)
        return (int(w), int(h))
    except Exception:
        return (0, 0)


def _get_ns_workspace() -> Any:
    """Get NSWorkspace class via pyobjc."""
    try:
        from AppKit import NSWorkspace
        return NSWorkspace
    except ImportError:
        try:
            import objc
            return objc.lookUpClass("NSWorkspace")
        except Exception:
            return None

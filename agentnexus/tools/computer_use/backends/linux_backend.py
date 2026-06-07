"""Linux accessibility backend using AT-SPI2 (gi.repository.Atspi).

Requires system packages:
    apt install python3-gi gir1.2-atspi-2.0

Works with both Xorg and Wayland.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from agentnexus.tools.computer_use.backends.base import ComputerUseBackend
from agentnexus.tools.computer_use.element import DesktopElement, normalize_role

logger = logging.getLogger(__name__)

# Lazy import guard
_atspi: Any = None


def _ensure_atspi() -> Any:
    """Import Atspi on demand."""
    global _atspi
    if _atspi is None:
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
            _atspi = Atspi
        except (ImportError, ValueError) as e:
            raise RuntimeError(
                "AT-SPI2 未安装。请执行: apt install python3-gi gir1.2-atspi-2.0"
            ) from e
    return _atspi


class LinuxBackend(ComputerUseBackend):
    """Linux AT-SPI2 backend via gi.repository.Atspi."""

    async def list_windows(self) -> list[dict[str, Any]]:
        """List all visible top-level windows."""
        Atspi = _ensure_atspi()
        desktop = Atspi.get_desktop(0)
        windows: list[dict[str, Any]] = []

        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            app_name = app.get_name() or ""
            for j in range(app.get_child_count()):
                window = app.get_child_at_index(j)
                if window is None:
                    continue
                role = window.get_role_name()
                if role not in ("frame", "dialog", "window"):
                    continue
                title = window.get_name() or ""
                try:
                    comp = window.query_interface("component")
                    if comp:
                        rect = comp.get_extents(0)  # ATSPI_COORD_TYPE_SCREEN
                        bounds = (rect.x, rect.y, rect.width, rect.height)
                    else:
                        bounds = (0, 0, 0, 0)
                except Exception:
                    bounds = (0, 0, 0, 0)
                windows.append({
                    "title": title,
                    "app_name": app_name,
                    "bounds": bounds,
                    "app_index": i,
                    "window_index": j,
                })

        return windows

    async def get_snapshot(
        self,
        app_name: str | None = None,
        window_title: str | None = None,
    ) -> list[DesktopElement]:
        """Get accessibility tree for matching windows."""
        Atspi = _ensure_atspi()
        desktop = Atspi.get_desktop(0)
        roots: list[DesktopElement] = []

        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            app_name_str = app.get_name() or ""
            if app_name and app_name.lower() not in app_name_str.lower():
                continue

            for j in range(app.get_child_count()):
                window = app.get_child_at_index(j)
                if window is None:
                    continue
                title = window.get_name() or ""
                if window_title and window_title.lower() not in title.lower():
                    continue

                elem = self._build_element(window)
                if elem:
                    roots.append(elem)

        return roots

    def _build_element(self, accessible: Any, depth: int = 0) -> DesktopElement | None:
        """Recursively build a DesktopElement from an Atspi.Accessible."""
        if depth > 30:
            return None

        try:
            role = accessible.get_role_name()
            name = accessible.get_name() or ""

            # Get value
            value = None
            try:
                val = accessible.get_value()
                if val is not None:
                    value = str(val)
            except Exception:
                pass

            # Get states
            states = accessible.get_state_set()
            enabled = states.contains(0)  # ATSPI_STATE_ENABLED
            focused = states.contains(12)  # ATSPI_STATE_FOCUSED
            checked = None
            if states.contains(1):  # ATSPI_STATE_CHECKABLE
                checked = states.contains(0)  # ATSPI_STATE_CHECKED

            # Get bounds
            try:
                comp = accessible.query_interface("component")
                if comp:
                    rect = comp.get_extents(0)
                    bounds = (rect.x, rect.y, rect.width, rect.height)
                else:
                    bounds = (0, 0, 0, 0)
            except Exception:
                bounds = (0, 0, 0, 0)

            # Build children
            children: list[DesktopElement] = []
            for k in range(accessible.get_child_count()):
                child = accessible.get_child_at_index(k)
                if child:
                    child_elem = self._build_element(child, depth + 1)
                    if child_elem:
                        children.append(child_elem)

            return DesktopElement(
                role=normalize_role("linux", role),
                name=name,
                value=value,
                enabled=enabled,
                focused=focused,
                checked=checked,
                bounds=bounds,
                children=tuple(children),
                platform_role=role,
                platform_id=accessible.get_path() or "",
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
        """Click an element using AT-SPI Action interface."""
        elem = self._find_element(element_id)
        action = elem.query_interface("action")
        if action:
            for _ in range(clicks):
                action.do_action(0)  # Default click action
        else:
            # Fallback: use xdotool
            comp = elem.query_interface("component")
            if comp:
                rect = comp.get_extents(0)
                x = rect.x + rect.width // 2
                y = rect.y + rect.height // 2
                btn = "3" if button == "right" else "1"
                subprocess.run(
                    ["xdotool", "mousemove", str(x), str(y), "click", "--repeat", str(clicks), btn],
                    check=False,
                )

    async def type_text(
        self,
        element_id: str,
        text: str,
        clear: bool = True,
    ) -> None:
        """Type text into an element."""
        elem = self._find_element(element_id)
        if clear:
            # Select all and delete
            try:
                text_iface = elem.query_interface("text")
                if text_iface:
                    text_iface.set_text_contents("")
            except Exception:
                pass

        # Use xdotool for typing
        subprocess.run(["xdotool", "type", "--clearmodifiers", text], check=False)

    async def press_key(self, keys: str) -> None:
        """Press a key combination."""
        # xdotool format: ctrl+alt+t
        subprocess.run(["xdotool", "key", keys], check=False)

    async def scroll(
        self,
        element_id: str | None,
        direction: str = "down",
        amount: int = 3,
    ) -> None:
        """Scroll an element or the screen."""
        if direction == "down":
            key = "Down"
        elif direction == "up":
            key = "Up"
        elif direction == "left":
            key = "Left"
        elif direction == "right":
            key = "Right"
        else:
            key = "Down"

        for _ in range(amount):
            subprocess.run(["xdotool", "key", f"Page_{key}"], check=False)

    async def select(
        self,
        element_id: str,
        value: str,
    ) -> None:
        """Select a value in a combobox."""
        elem = self._find_element(element_id)
        action = elem.query_interface("action")
        if action:
            # Try to find and activate the matching child
            for i in range(elem.get_child_count()):
                child = elem.get_child_at_index(i)
                if child and value.lower() in (child.get_name() or "").lower():
                    child_action = child.query_interface("action")
                    if child_action:
                        child_action.do_action(0)
                    return
        raise ValueError(f"找不到选项: {value}")

    async def toggle(
        self,
        element_id: str,
        checked: bool | None = None,
    ) -> None:
        """Toggle a checkbox."""
        elem = self._find_element(element_id)
        action = elem.query_interface("action")
        if action:
            action.do_action(0)

    async def launch_app(
        self,
        app_path: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """Launch an application."""
        cmd = [app_path] + (args or [])
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
            try:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, text=True, timeout=5,
                )
                return result.stdout
            except FileNotFoundError:
                return ""

    async def set_clipboard(self, text: str) -> None:
        """Write to clipboard."""
        try:
            import pyperclip
            pyperclip.copy(text)
        except ImportError:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode(), check=False,
            )

    def _find_element(self, element_id: str) -> Any:
        """Find an AT-SPI element by path or name."""
        Atspi = _ensure_atspi()
        desktop = Atspi.get_desktop(0)

        # Search by path
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app:
                result = self._search_element(app, element_id)
                if result:
                    return result

        raise ValueError(f"找不到元素: {element_id}")

    def _search_element(self, accessible: Any, target: str) -> Any:
        """Recursively search for an element by platform_id or name."""
        try:
            if (accessible.get_path() or "") == target:
                return accessible
            if (accessible.get_name() or "") == target:
                return accessible
        except Exception:
            pass

        for i in range(accessible.get_child_count()):
            child = accessible.get_child_at_index(i)
            if child:
                result = self._search_element(child, target)
                if result:
                    return result
        return None

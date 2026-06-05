"""Browser automation via Playwright — headless Chromium control with accessibility tree."""

from __future__ import annotations

import asyncio
import logging
import re
from time import monotonic
from typing import Any

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Locator,
        Page,
        Playwright,
        async_playwright,
    )
except ImportError:
    Browser = None  # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]
    Locator = None  # type: ignore[assignment,misc]
    Page = None  # type: ignore[assignment,misc]
    Playwright = None  # type: ignore[assignment,misc]
    async_playwright = None  # type: ignore[assignment,misc]

from agentnexus.core.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "searchbox", "combobox",
    "checkbox", "radio", "switch", "slider", "spinbutton",
    "menuitem", "menuitemcheckbox", "menuitemradio",
    "tab", "option", "scrollbar", "tablist",
}

READING_ROLES = INTERACTIVE_ROLES | {
    "heading", "paragraph", "status", "alert", "log",
    "marquee", "timer", "note", "definition",
}

LANDMARK_ROLES = {
    "banner", "complementary", "contentinfo", "form",
    "main", "navigation", "region", "search",
}

# ---------------------------------------------------------------------------
# Error formatting helpers
# ---------------------------------------------------------------------------


def _error(msg: str, detail: str = "", hint: str = "") -> str:
    """Format a unified error response."""
    parts = [f"ERROR: {msg}"]
    if detail:
        parts.append(f"DETAIL: {detail}")
    if hint:
        parts.append(f"HINT: {hint}")
    return "\n".join(parts)


def _warning(msg: str, detail: str = "") -> str:
    """Format a unified warning response."""
    parts = [f"WARNING: {msg}"]
    if detail:
        parts.append(f"DETAIL: {detail}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# YAML parsing helpers for aria_snapshot output
# ---------------------------------------------------------------------------

# aria_snapshot YAML patterns:
# - button "Login" [ref=s1e3]
# - textbox "Search" [ref=s1e4] [checked]
# - link "About" [ref=s1e5]
# - heading "Welcome" [level=1] [ref=s1e1]
# - generic [ref=s2e1]:
#     - paragraph [ref=s2e2]:
#         - text: Hello world
_YAML_NODE_RE = re.compile(
    r'^(\s*)- (\w+)'           # indent + role
    r'(?:\s+"([^"]*)")?'       # optional "name"
    r'(?:\s*\[ref=([^\]]+)\])?' # optional [ref=...]
    r'(?:\s*\[([^\]]+)\])?'    # optional [extra attrs]
    r'(?:\s*:\s*)?$',          # optional colon for parent nodes
)


def _parse_aria_yaml(raw: str) -> list[dict]:
    """Parse aria_snapshot YAML output into a flat list of node dicts.

    Returns list of {role, name, ref, attrs, depth}.
    """
    nodes: list[dict] = []
    for line in raw.splitlines():
        m = _YAML_NODE_RE.match(line)
        if not m:
            continue
        indent, role, name, ref, attrs = m.groups()
        depth = len(indent) // 2 if indent else 0
        nodes.append({
            "role": role,
            "name": name or "",
            "ref": ref or "",
            "attrs": attrs or "",
            "depth": depth,
        })
    return nodes


def _format_a11y_tree(nodes: list[dict], start_idx: int = 1) -> str:
    """Format parsed nodes into numbered text for LLM consumption.

    Format:
      Named element:   [1] button "Login" ref=e123 [visible]
      Unnamed element: [2] textbox [box=400,620,760,24] [visible]  ← 无名称，用 pos="400,620,760,24" 操作
    """
    lines: list[str] = []
    for i, n in enumerate(nodes, start=start_idx):
        role = n["role"]
        name = n["name"]
        ref = n.get("ref", "")
        attrs = n.get("attrs", "")
        viewport = n.get("viewport_status", "")
        box = n.get("box")

        # Build line
        parts = [f"[{i}]"]
        parts.append(role)
        if name:
            parts.append(f'"{name}"')
        if ref:
            parts.append(f"ref={ref}")
        if attrs:
            parts.append(f"[{attrs}]")
        # Show box prominently for unnamed elements (Agent needs it for pos parameter)
        if box and not name:
            parts.append(f"[box={','.join(str(b) for b in box)}]")
        elif box:
            parts.append(f"[box={','.join(str(b) for b in box)}]")
        if viewport:
            parts.append(f"[{viewport}]")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _in_viewport(box: list[float] | None, vp_w: int, vp_h: int) -> bool:
    """Check if element bounding box intersects with viewport."""
    if not box or len(box) != 4:
        return True
    x, y, w, h = box
    return x < vp_w and y < vp_h and x + w > 0 and y + h > 0


def _truncate_by_priority(
    nodes: list[dict],
    max_nodes: int,
) -> list[dict]:
    """Truncate nodes by priority: in-viewport interactive > in-viewport reading > offscreen.

    Uses viewport_status field (set by get_a11y_tree) to determine visibility.
    """
    if len(nodes) <= max_nodes:
        return nodes

    bucket_vp_interactive = []
    bucket_vp_reading = []
    bucket_offscreen = []

    for n in nodes:
        in_vp = n.get("viewport_status") != "below viewport"
        if in_vp and n.get("role") in INTERACTIVE_ROLES:
            bucket_vp_interactive.append(n)
        elif in_vp and n.get("role") in READING_ROLES:
            bucket_vp_reading.append(n)
        else:
            bucket_offscreen.append(n)

    result = []
    remaining = max_nodes
    for bucket in [bucket_vp_interactive, bucket_vp_reading, bucket_offscreen]:
        if remaining <= 0:
            break
        take = bucket[:remaining]
        result.extend(take)
        remaining -= len(take)

    return result


def _check_hitl_rules(action: str, role: str | None, name: str | None) -> bool:
    """Check if an action matches any HITL rule requiring human confirmation.

    Returns True if the action should be blocked for HITL review.
    """
    settings = get_settings()
    rules = settings.browser_hitl_rules
    if not rules:
        return False

    for rule in rules:
        if rule.get("action") and rule["action"] != action:
            continue
        if rule.get("role") and rule.get("role") != (role or ""):
            continue
        if rule.get("name_pattern"):
            import re as _re
            if not _re.search(rule["name_pattern"], name or "", _re.IGNORECASE):
                continue
        return True  # Rule matched — HITL required
    return False


def _is_closed_error(exc: Exception) -> bool:
    """Check if an exception indicates the browser/context/page was closed externally."""
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "target page, context or browser has been closed",
        "browser has been closed",
        "session deleted",
        "target closed",
        "connection closed",
    ))


# ---------------------------------------------------------------------------
# BrowserManager — singleton browser + per-task page isolation
# ---------------------------------------------------------------------------


class BrowserManager:
    """Singleton browser manager with per-task page isolation.

    Modes:
    - isolated: Launch a fresh Chromium instance, each task gets its own BrowserContext
    - cdp: Connect to user's running browser via CDP, share user's context, each task gets a new Page
    """

    _instance: BrowserManager | None = None

    def __init__(self) -> None:
        self._mode: str = "isolated"
        self._browser: Any = None  # Browser
        self._playwright: Any = None  # Playwright
        self._lock = asyncio.Lock()
        # per-task resources
        self._contexts: dict[str, Any] = {}  # task_id -> BrowserContext (isolated mode)
        self._shared_context: Any = None  # BrowserContext (cdp mode)
        self._pages: dict[str, list[Any]] = {}  # task_id -> [Page, ...] (multiple tabs)
        self._active_page_idx: dict[str, int] = {}  # task_id -> active page index
        self._pending_new_page: dict[str, Any] = {}  # task_id -> newly opened Page (to be consumed)
        self._last_access: dict[str, float] = {}  # task_id -> monotonic timestamp
        self._active_tasks: set[str] = set()
        self._ttl_task: Any = None  # asyncio.Task for TTL cleanup
        self._browser_ready = False
        self._ttl_enabled: bool = True  # Set to False to disable TTL cleanup (for testing)
        self._evicted_snapshots: dict[str, dict] = {}  # task_id -> {url, title, evicted_at}

    @classmethod
    def instance(cls) -> BrowserManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def ensure_browser(self) -> Any:
        """Lazily initialize the browser instance (acquires lock)."""
        if self._browser_ready and self._browser is not None:
            return self._browser

        async with self._lock:
            return await self._ensure_browser_inner()

    async def _ensure_browser_inner(self) -> Any:
        """Internal: initialize browser. Caller must hold self._lock."""
        if self._browser_ready and self._browser is not None:
            return self._browser

        settings = get_settings()
        self._mode = settings.browser_mode

        if async_playwright is None:
            raise RuntimeError("playwright 未安装。请执行: pip install playwright && playwright install chromium")

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if self._mode == "cdp":
            endpoint = settings.browser_cdp_endpoint
            # 先尝试连接已有 Chrome 实例
            try:
                logger.info("Connecting to browser via CDP: %s", endpoint)
                self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
                logger.info("CDP connected, contexts: %d", len(self._browser.contexts))
            except Exception:
                # 连不上，自动启动带调试端口的 Chrome
                import re as _re
                port_match = _re.search(r":(\d+)$", endpoint)
                port = port_match.group(1) if port_match else "9222"
                logger.info("CDP connection failed, launching Chrome with --remote-debugging-port=%s", port)
                self._browser = await self._playwright.chromium.launch(
                    headless=False,
                    args=[f"--remote-debugging-port={port}"],
                )
                logger.info("Chrome launched with CDP on port %s", port)
        else:
            logger.info("Launching Chromium (isolated mode, headless=%s)", settings.browser_headless)
            self._browser = await self._playwright.chromium.launch(
                headless=settings.browser_headless,
            )

        self._browser_ready = True

        # Start TTL cleanup background task (skippable for testing)
        if self._ttl_enabled and self._ttl_task is None:
            self._ttl_task = asyncio.create_task(self._ttl_cleanup_loop())

        return self._browser

    async def get_page(self, task_id: str) -> Any:
        """Get or create a Page for the given task_id.

        - isolated mode: creates a new BrowserContext + Page per task
        - cdp mode: reuses user's existing context, creates a new Page per task
        - registers context.on('page') listener for new tab detection
        """
        if task_id in self._pages:
            self._last_access[task_id] = monotonic()
            self._active_tasks.add(task_id)
            idx = self._active_page_idx.get(task_id, 0)
            return self._pages[task_id][idx]

        async with self._lock:
            # Double-check after acquiring lock
            if task_id in self._pages:
                self._last_access[task_id] = monotonic()
                self._active_tasks.add(task_id)
                idx = self._active_page_idx.get(task_id, 0)
                return self._pages[task_id][idx]

            browser = await self._ensure_browser_inner()
            settings = get_settings()

            if self._mode == "cdp":
                # Reuse user's existing context
                if self._shared_context is None:
                    if browser.contexts:
                        self._shared_context = browser.contexts[0]
                        cdp_url = self._shared_context.pages[0].url if self._shared_context.pages else "N/A"
                        logger.info("CDP: reusing existing context (url=%s)", cdp_url)
                    else:
                        self._shared_context = await browser.new_context()
                        logger.info("CDP: no existing context found, created new one")
                ctx = self._shared_context
            else:
                # Isolated mode: new context per task
                ctx = await browser.new_context(
                    viewport={
                        "width": settings.browser_viewport_width,
                        "height": settings.browser_viewport_height,
                    },
                )
                self._contexts[task_id] = ctx

            # Register new-tab listener on this context
            ctx.on("page", lambda page: self._on_new_page(task_id, page))

            page = await ctx.new_page()
            self._pages[task_id] = [page]
            self._active_page_idx[task_id] = 0
            self._last_access[task_id] = monotonic()
            self._active_tasks.add(task_id)
            return page

    def _on_new_page(self, task_id: str, page: Any) -> None:
        """Callback when BrowserContext opens a new tab/page.

        Appends the page to the task's page list, switches active index to it,
        and marks it as pending for notification.
        """
        if task_id not in self._pages:
            self._pages[task_id] = []
        self._pages[task_id].append(page)
        self._active_page_idx[task_id] = len(self._pages[task_id]) - 1
        self._pending_new_page[task_id] = page
        logger.info("New tab detected for task %s: %s", task_id, page.url)

    def consume_pending_new_page(self, task_id: str) -> Any:
        """Pop and return the pending new page for a task, or None."""
        return self._pending_new_page.pop(task_id, None)

    async def list_pages(self, task_id: str) -> list[dict]:
        """List all open pages for a task with their url, title, and active status."""
        pages = self._pages.get(task_id, [])
        active_idx = self._active_page_idx.get(task_id, 0)
        result = []
        for i, page in enumerate(pages):
            try:
                url = page.url
                title = await page.title()
            except Exception:
                url = "unknown"
                title = "unknown"
            result.append({
                "index": i,
                "url": url,
                "title": title,
                "active": i == active_idx,
            })
        return result

    async def switch_page(self, task_id: str, index: int) -> Any:
        """Switch the active page for a task by index. Returns the Page object."""
        pages = self._pages.get(task_id, [])
        if not pages:
            raise ValueError(f"任务 {task_id} 没有打开的页面。")
        if index < 0 or index >= len(pages):
            raise ValueError(
                f"页面索引 {index} 超出范围（共 {len(pages)} 个页面，索引 0-{len(pages)-1}）。"
                f"请先调用 browser_list_pages() 查看所有打开的标签页。"
            )
        self._active_page_idx[task_id] = index
        self._last_access[task_id] = monotonic()
        return pages[index]

    async def navigate(self, task_id: str, url: str, wait_until: str = "load") -> dict:
        """Navigate to URL. Returns dict with title, url, readyState, timed_out."""
        page = await self.get_page(task_id)
        settings = get_settings()
        timeout = settings.browser_default_timeout

        timed_out = False
        if wait_until == "networkidle":
            # networkidle has a separate shorter timeout
            ni_timeout = settings.browser_networkidle_timeout
            try:
                await asyncio.wait_for(
                    page.goto(url, wait_until="load", timeout=timeout),
                    timeout=timeout / 1000,
                )
                # Try networkidle with separate short timeout
                try:
                    await asyncio.wait_for(
                        page.wait_for_load_state("networkidle"),
                        timeout=ni_timeout / 1000,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    logger.warning("networkidle timed out (%dms), continuing", ni_timeout)
            except Exception as e:
                return {"title": "", "url": url, "readyState": "unknown", "error": str(e), "timed_out": True}
        else:
            try:
                await page.goto(url, wait_until=wait_until, timeout=timeout)
            except Exception as e:
                return {"title": "", "url": url, "readyState": "unknown", "error": str(e), "timed_out": True}

        title = await page.title()
        ready_state = await page.evaluate("document.readyState")
        return {
            "title": title,
            "url": page.url,
            "readyState": ready_state,
            "timed_out": timed_out,
        }

    async def close_task(self, task_id: str) -> None:
        """Close all pages and context for a task."""
        self._active_tasks.discard(task_id)

        # Close all pages (not just the active one)
        pages = self._pages.pop(task_id, [])
        for page in pages:
            try:
                await page.close()
            except Exception:
                pass

        if self._mode == "isolated":
            ctx = self._contexts.pop(task_id, None)
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass

        self._active_page_idx.pop(task_id, None)
        self._pending_new_page.pop(task_id, None)
        self._last_access.pop(task_id, None)

    async def mark_task_inactive(self, task_id: str) -> None:
        """Mark a task as inactive (allows TTL cleanup)."""
        self._active_tasks.discard(task_id)

    async def _save_task_snapshot(self, task_id: str) -> dict:
        """Save active page state before eviction for potential recovery."""
        pages = self._pages.get(task_id, [])
        if not pages:
            return {}
        idx = self._active_page_idx.get(task_id, 0)
        page = pages[idx]
        try:
            return {
                "url": page.url,
                "title": await page.title(),
                "evicted_at": monotonic(),
            }
        except Exception:
            return {"url": "unknown", "evicted_at": monotonic()}

    def get_evicted_snapshot(self, task_id: str) -> dict | None:
        """Retrieve saved state of an evicted task. Returns None if not found."""
        return self._evicted_snapshots.pop(task_id, None)

    async def reset_stale_browser(self, task_id: str) -> None:
        """Clear stale state when browser/context/page was closed externally.

        After calling this, the next get_page() will reinitialize the browser.
        """
        # Clear this task's pages and index
        self._pages.pop(task_id, [])
        self._active_page_idx.pop(task_id, None)
        self._pending_new_page.pop(task_id, None)
        self._contexts.pop(task_id, None)

        # Try to close the old browser instance (ignore errors — it may already be dead)
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        # Reset flag so next get_page() triggers _ensure_browser_inner
        self._browser_ready = False
        logger.info("Stale browser state cleared for task %s, will reinitialize", task_id)

    async def close_all(self) -> None:
        """Close all task resources and the browser."""
        # Cancel TTL task
        if self._ttl_task:
            self._ttl_task.cancel()
            try:
                await self._ttl_task
            except asyncio.CancelledError:
                pass
            self._ttl_task = None

        # Close all pages
        for task_id in list(self._pages.keys()):
            await self.close_task(task_id)

        # Close shared context (cdp mode)
        if self._shared_context:
            try:
                # Don't close user's actual context, just our reference
                self._shared_context = None
            except Exception:
                pass

        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._browser_ready = False

        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        BrowserManager._instance = None

    async def _ttl_cleanup_loop(self) -> None:
        """Background task: periodically clean up idle task resources.

        Before eviction, saves a snapshot (url + title) so the LLM can
        recover the page state if needed.
        """
        try:
            while True:
                await asyncio.sleep(60)
                settings = get_settings()
                ttl = settings.browser_context_ttl
                now = monotonic()
                for task_id in list(self._last_access.keys()):
                    if task_id in self._active_tasks:
                        continue
                    elapsed = now - self._last_access.get(task_id, 0)
                    if elapsed > ttl:
                        # Save state before eviction for recovery
                        snapshot = await self._save_task_snapshot(task_id)
                        self._evicted_snapshots[task_id] = snapshot
                        logger.info(
                            "TTL eviction: task %s (idle %.0fs), snapshot saved (url=%s)",
                            task_id, elapsed, snapshot.get("url", "unknown"),
                        )
                        await self.close_task(task_id)
        except asyncio.CancelledError:
            return

    async def _wait_dom_stable(
        self,
        page: Any,
        timeout: int = 3000,
        poll_interval: float = 0.3,
    ) -> None:
        """Wait until two consecutive aria_snapshot calls return identical output.

        This ensures the DOM has settled before taking a snapshot, avoiding
        capturing intermediate SPA rendering states (loading spinners, empty shells).
        Times out gracefully without blocking.
        """
        deadline = monotonic() + timeout / 1000
        prev_hash = ""
        root = page.locator("body")

        while monotonic() < deadline:
            try:
                raw = await root.aria_snapshot(mode="ai", boxes=True)
                curr_hash = hash(raw)
                if curr_hash == prev_hash and prev_hash != "":
                    return  # Two consecutive snapshots match — DOM is stable
                prev_hash = curr_hash
            except Exception:
                pass
            await asyncio.sleep(poll_interval)

        logger.debug("DOM stability timeout (%dms), proceeding with current state", timeout)

    async def get_a11y_tree(
        self,
        task_id: str,
        scope: str | None = None,
        mode: str = "reading",
        include_offscreen: bool = False,
        wait_stable: bool = True,
    ) -> dict:
        """Extract accessibility tree with filtering.

        Args:
            wait_stable: If True, wait for DOM to stabilize before snapshot.
                Set to False for fast polling scenarios.

        Returns {"skeleton": [...], "detail": [...]}.
        """
        page = await self.get_page(task_id)
        settings = get_settings()
        max_nodes = settings.browser_snapshot_max_nodes

        # Scope: use locator if specified
        if scope:
            root = page.locator(scope)
        else:
            root = page.locator("body")

        # Wait for DOM to stabilize before snapshotting
        if wait_stable:
            await self._wait_dom_stable(page)

        # Get a11y tree via aria_snapshot (Playwright 1.59+)
        try:
            raw_yaml = await root.aria_snapshot(mode="ai", boxes=True)
        except Exception as e:
            return {"skeleton": [], "detail": [], "error": str(e)}

        # Parse YAML output
        all_nodes = _parse_aria_yaml(raw_yaml)

        # Build skeleton (landmarks + headings)
        skeleton = [n for n in all_nodes if n.get("role") in LANDMARK_ROLES or n.get("role") == "heading"]

        # Filter detail by mode
        NON_GENERIC_ROLES = INTERACTIVE_ROLES | READING_ROLES | LANDMARK_ROLES | {
            "img", "list", "listitem", "table", "row", "cell",
            "separator", "toolbar", "tree", "treeitem", "grid", "gridcell",
        }
        if mode == "interactive":
            detail = [n for n in all_nodes if n.get("role") in INTERACTIVE_ROLES]
        elif mode == "reading":
            detail = [n for n in all_nodes if n.get("role") in READING_ROLES]
        else:  # full — all semantic roles, excluding generic/none/presentation
            detail = [n for n in all_nodes if n.get("role") in NON_GENERIC_ROLES]

        # Viewport marking
        vp = page.viewport_size
        vp_w = vp["width"] if vp else 1280
        vp_h = vp["height"] if vp else 720

        for n in detail:
            box = n.get("box")
            if box and not _in_viewport(box, vp_w, vp_h):
                n["viewport_status"] = "below viewport"
            else:
                n["viewport_status"] = "visible"

        # Priority-based truncation: in-viewport interactive > in-viewport reading > offscreen
        if not include_offscreen:
            detail = [n for n in detail if n.get("viewport_status") != "below viewport"]
        detail = _truncate_by_priority(detail, max_nodes)

        return {"skeleton": skeleton, "detail": detail}

    async def find_element(
        self,
        task_id: str,
        ref: str | None = None,
        role: str | None = None,
        name: str | None = None,
        selector: str | None = None,
        pos: str | None = None,
    ) -> Any:
        """Find an element by priority: pos > role+name > name > selector.

        Args:
            pos: "x,y,w,h" box coordinates from aria_snapshot. Most reliable method.
            role: ARIA role (button, link, textbox, etc.)
            name: Element accessible name or visible text
            selector: CSS selector (fallback)
            ref: Display label from snapshot (NOT usable for locating, kept for error messages)

        Returns a Playwright Locator, or None if pos is used (caller handles coordinate click).
        """
        page = await self.get_page(task_id)

        # pos: coordinate-based — most reliable, works for unnamed elements
        if pos:
            return pos  # Return raw coords, caller does mouse.click

        if role and name:
            try:
                loc = page.get_by_role(role, name=name, exact=False)
                if await loc.count() > 0:
                    return loc.first
            except Exception:
                pass
            try:
                loc = page.get_by_role(role, name=name, exact=True)
                if await loc.count() > 0:
                    return loc.first
            except Exception:
                pass
            raise ValueError(
                f"找不到元素 role={role} name={name}。"
                "请调用 browser_snapshot() 查看当前页面可交互元素。"
            )

        if name:
            try:
                loc = page.get_by_text(name, exact=False)
                if await loc.count() > 0:
                    return loc.first
            except Exception:
                pass
            raise ValueError(
                f"找不到包含文本 \"{name}\" 的元素。"
                "请调用 browser_snapshot() 查看当前页面可交互元素。"
            )

        if selector:
            try:
                loc = page.locator(selector)
                if await loc.count() > 0:
                    return loc.first
            except Exception:
                pass
            raise ValueError(f"找不到选择器 {selector} 对应的元素。")

        if ref:
            raise ValueError(
                "ref 是显示标签，无法用于定位。请改用 pos 坐标或 role+name。"
                "从 snapshot 中复制 [box=x,y,w,h] 传给 pos 参数。"
            )

        raise ValueError("必须提供 pos、role+name、name 或 selector 中的至少一个参数。")


# ---------------------------------------------------------------------------
# Persistent background event loop for Playwright
# ---------------------------------------------------------------------------

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_thread: Any = None


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    """Get or create a persistent background event loop running in a daemon thread.

    Playwright connections are tied to the event loop they were created on.
    Using asyncio.run() per-call creates a new loop each time, breaking the connection.
    This function maintains a single persistent loop for all browser operations.
    """
    global _bg_loop, _bg_thread
    if _bg_loop is not None and _bg_loop.is_running():
        return _bg_loop

    import threading

    _bg_loop = asyncio.new_event_loop()

    def _run_loop():
        asyncio.set_event_loop(_bg_loop)
        _bg_loop.run_forever()

    _bg_thread = threading.Thread(target=_run_loop, daemon=True, name="browser-bg-loop")
    _bg_thread.start()
    return _bg_loop


def _run_async(coro) -> Any:
    """Run an async coroutine on the persistent background event loop.

    ToolRegistry calls tools synchronously via ThreadPoolExecutor.
    This wrapper dispatches the coroutine to the persistent background loop
    where Playwright was initialized, keeping connections alive.
    """
    loop = _get_bg_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=120)


# ---------------------------------------------------------------------------
# Async implementations (internal)
# ---------------------------------------------------------------------------

async def _with_browser_recovery(coro_factory, task_id: str):
    """Execute an async operation with automatic recovery on browser closed errors.

    Args:
        coro_factory: A callable that returns a coroutine (called fresh for retry)
        task_id: The task ID for recovery context

    Returns:
        The result of the coroutine, or raises if non-closed error or retry fails.
    """
    try:
        return await coro_factory()
    except Exception as e:
        if _is_closed_error(e):
            logger.warning("Browser closed externally, resetting and retrying for task %s", task_id)
            mgr = BrowserManager.instance()
            await mgr.reset_stale_browser(task_id)
            return await coro_factory()
        raise


async def _async_navigate(url: str, wait_until: str, task_id: str) -> str:
    if wait_until not in ("load", "domcontentloaded", "networkidle"):
        wait_until = "load"

    mgr = BrowserManager.instance()

    result = await _with_browser_recovery(
        lambda: mgr.navigate(task_id, url, wait_until), task_id,
    )

    if "error" in result:
        return _error(
            f"导航失败: {result['error']}",
            detail=f"URL: {url}",
            hint="检查 URL 是否可访问，或尝试 wait_until='domcontentloaded'",
        )

    parts = [f"已导航至: {result['url']}", f"标题: {result['title']}", f"readyState: {result['readyState']}"]

    if result.get("timed_out"):
        parts.insert(0, _warning(
            "networkidle 超时，已 fallback 继续执行",
            detail="页面仍有活跃的网络连接，可能为 WebSocket 或长轮询。"
                   "请调用 browser_snapshot() 确认页面就绪后再操作。",
        ))

    # Include skeleton for quick orientation
    try:
        tree = await mgr.get_a11y_tree(task_id, mode="interactive")
        skeleton = tree.get("skeleton", [])
        if skeleton:
            parts.append("\n## 页面结构")
            parts.append(_format_a11y_tree(skeleton))
    except Exception:
        pass

    return "\n".join(parts)


async def _async_snapshot(
    scope: str | None, mode: str, include_offscreen: bool, task_id: str,
    wait_stable: bool = True,
) -> str:
    if mode not in ("interactive", "reading", "full"):
        mode = "reading"

    mgr = BrowserManager.instance()
    try:
        tree = await _with_browser_recovery(
            lambda: mgr.get_a11y_tree(
                task_id, scope=scope, mode=mode,
                include_offscreen=include_offscreen, wait_stable=wait_stable,
            ),
            task_id,
        )
    except Exception as e:
        return _error(f"获取页面快照失败: {e}")

    parts: list[str] = []

    skeleton = tree.get("skeleton", [])
    if skeleton:
        parts.append("## 页面结构")
        parts.append(_format_a11y_tree(skeleton))
        parts.append("")

    detail = tree.get("detail", [])
    if detail:
        parts.append(f"## 可交互元素 (mode={mode})")
        parts.append(_format_a11y_tree(detail))
    else:
        parts.append("当前页面无可交互元素。")

    if tree.get("error"):
        parts.append(f"\n[警告] a11y tree 提取异常: {tree['error']}")

    return "\n".join(parts)


async def _post_click_result(mgr: BrowserManager, task_id: str, base_msg: str) -> str:
    """After a click, briefly wait for a new tab, then return result with tab info if any."""
    # Give the browser a moment to open a new tab
    await asyncio.sleep(1.0)
    new_page = mgr.consume_pending_new_page(task_id)
    if new_page:
        try:
            title = await new_page.title()
            url = new_page.url
        except Exception:
            title = "unknown"
            url = "unknown"
        return (
            f"{base_msg}\n"
            f"点击打开了新标签页: \"{title}\" ({url})，已自动切换到新标签页。"
        )
    return base_msg


async def _async_click(ref, role, name, selector, double_click, pos, task_id) -> str:
    # HITL gate: check if this click matches a human-in-the-loop rule
    if _check_hitl_rules("click", role, name):
        return _warning(
            f"操作需要人工确认: click role={role} name={name}",
            detail="此操作匹配了 HITL 安全规则。",
        ) + "\nCONFIRMATION_REQUIRED"

    mgr = BrowserManager.instance()
    max_retries = 2

    for attempt in range(max_retries + 1):
        try:
            result = await mgr.find_element(
                task_id, ref=ref, role=role, name=name, selector=selector, pos=pos,
            )
        except ValueError as e:
            if attempt < max_retries:
                await asyncio.sleep(0.5)  # Element may be loading
                continue
            return _error(str(e))
        except Exception as e:
            if _is_closed_error(e):
                await mgr.reset_stale_browser(task_id)
                return _error(
                    "浏览器已关闭，已自动重置。请重新导航到目标页面后再操作。",
                    hint="调用 browser_navigate(url) 打开新页面。",
                )
            return _error(f"查找元素失败: {e}")

        try:
            # pos returns raw "x,y,w,h" string — do coordinate click
            if isinstance(result, str):
                parts = result.split(",")
                if len(parts) == 4:
                    x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                    cx, cy = x + w // 2, y + h // 2
                    page = await mgr.get_page(task_id)
                    if double_click:
                        await page.mouse.dblclick(cx, cy)
                    else:
                        await page.mouse.click(cx, cy)
                    return await _post_click_result(mgr, task_id, f"已点击坐标 ({cx}, {cy})。")
                return _error(f"pos 格式错误，需要 x,y,w,h，收到: {result}")

            if double_click:
                await result.dblclick(timeout=5000)
            else:
                await result.click(timeout=5000)
            return await _post_click_result(mgr, task_id, "已点击元素。")
        except Exception as e:
            if _is_closed_error(e):
                await mgr.reset_stale_browser(task_id)
                return _error(
                    "浏览器已关闭，已自动重置。请重新导航到目标页面后再操作。",
                    hint="调用 browser_navigate(url) 打开新页面。",
                )
            if attempt < max_retries:
                await asyncio.sleep(0.5)
                continue
            return _error(
                f"点击失败: {e}",
                hint="元素可能不可见或被遮挡。尝试用 pos 坐标点击: browser_click(pos='x,y,w,h')。",
            )

    return _error("点击失败: 超过最大重试次数。")


async def _async_type(ref, role, name, selector, text, clear, press_enter, pos, task_id) -> str:
    # HITL gate: check if this typing action matches a human-in-the-loop rule
    if _check_hitl_rules("type", role, name):
        return _warning(
            f"操作需要人工确认: type role={role} name={name}",
            detail="此操作匹配了 HITL 安全规则。",
        ) + "\nCONFIRMATION_REQUIRED"

    mgr = BrowserManager.instance()
    max_retries = 2

    for attempt in range(max_retries + 1):
        try:
            result = await mgr.find_element(
                task_id, ref=ref, role=role, name=name, selector=selector, pos=pos,
            )
        except ValueError as e:
            if attempt < max_retries:
                await asyncio.sleep(0.5)
                continue
            return _error(str(e))
        except Exception as e:
            if _is_closed_error(e):
                await mgr.reset_stale_browser(task_id)
                return _error(
                    "浏览器已关闭，已自动重置。请重新导航到目标页面后再操作。",
                    hint="调用 browser_navigate(url) 打开新页面。",
                )
            return _error(f"查找元素失败: {e}")

        try:
            # pos returns raw "x,y,w,h" string — click to focus, then type
            if isinstance(result, str):
                parts = result.split(",")
                if len(parts) == 4:
                    x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                    cx, cy = x + w // 2, y + h // 2
                    page = await mgr.get_page(task_id)
                    await page.mouse.click(cx, cy)
                    if clear:
                        await page.keyboard.press("Control+a")
                    await page.keyboard.type(text)
                    if press_enter:
                        await page.keyboard.press("Enter")
                    return "已输入文本。"
                return _error(f"pos 格式错误，需要 x,y,w,h，收到: {result}")

            if clear:
                await result.clear(timeout=5000)
            await result.fill(text, timeout=5000)
            if press_enter:
                await result.press("Enter")
            return "已输入文本。"
        except Exception as e:
            if _is_closed_error(e):
                await mgr.reset_stale_browser(task_id)
                return _error(
                    "浏览器已关闭，已自动重置。请重新导航到目标页面后再操作。",
                    hint="调用 browser_navigate(url) 打开新页面。",
                )
            if attempt < max_retries:
                await asyncio.sleep(0.5)
                continue
            return _error(f"输入失败: {e}")

    return _error("输入失败: 超过最大重试次数。")


async def _async_read(selector, ref, max_chars, task_id) -> str:
    mgr = BrowserManager.instance()
    page = await _with_browser_recovery(lambda: mgr.get_page(task_id), task_id)

    try:
        if ref:
            loc = await mgr.find_element(task_id, ref=ref)
        elif selector:
            loc = page.locator(selector)
        else:
            loc = page.locator("body")
        text = await loc.inner_text(timeout=5000)
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        return _error(f"读取内容失败: {e}")

    if len(text) > max_chars:
        total = len(text)
        text = text[:max_chars]
        return f"{text}\n\n... (内容已截断，共 {total} 字符，已显示前 {max_chars} 字符)"
    return text


async def _async_screenshot(path, full_page, task_id) -> str:
    import os
    from pathlib import Path

    mgr = BrowserManager.instance()
    page = await _with_browser_recovery(lambda: mgr.get_page(task_id), task_id)

    settings = get_settings()
    if not path:
        ss_dir = settings.browser_screenshot_dir
        if not ss_dir:
            ss_dir = str(Path.home() / ".agentnexus" / "screenshots")
        os.makedirs(ss_dir, exist_ok=True)
        ts = int(monotonic() * 1000)
        path = os.path.join(ss_dir, f"screenshot_{ts}.png")

    try:
        await page.screenshot(path=path, full_page=full_page)
        return f"截图已保存: {path}"
    except Exception as e:
        return _error(f"截图失败: {e}")


async def _async_evaluate(expression, task_id) -> str:
    settings = get_settings()
    if not settings.browser_allow_js_execution:
        return _error(
            "JS 执行未启用",
            detail="当前配置禁止执行 JavaScript",
            hint="在 config.yaml 中设置 browser_allow_js_execution: true 以启用",
        )

    mgr = BrowserManager.instance()
    page = await _with_browser_recovery(lambda: mgr.get_page(task_id), task_id)

    try:
        result = await page.evaluate(expression)
        return f"执行结果: {result}"
    except Exception as e:
        return _error(f"JS 执行失败: {e}")


async def _async_wait(role, name, ref, text, timeout, task_id) -> str:
    if not any([role, name, ref, text]):
        return _error("必须指定 role+name、ref 或 text 中的至少一个参数。")

    mgr = BrowserManager.instance()
    deadline = monotonic() + timeout / 1000
    poll_interval = 0.5

    while monotonic() < deadline:
        # Use wait_stable=False for fast polling — we just need the current state
        tree = await mgr.get_a11y_tree(task_id, mode="reading", wait_stable=False)
        nodes = tree.get("detail", [])

        for node in nodes:
            if ref and node.get("ref") == ref:
                return f"元素 ref={ref} 已出现。"
            if role and name and node.get("role") == role:
                if name.lower() in node.get("name", "").lower():
                    return f"元素 role={role} name={name} 已出现。"
            if text and text.lower() in node.get("name", "").lower():
                return f"文本 \"{text}\" 已出现。"

        await asyncio.sleep(poll_interval)

    return _warning(f"等待超时 ({timeout}ms): 未找到匹配元素")


async def _async_scroll(direction, amount, task_id) -> str:
    if direction not in ("up", "down", "left", "right"):
        return _error(f"不支持的滚动方向: {direction}，可选: up, down, left, right")

    mgr = BrowserManager.instance()
    page = await _with_browser_recovery(lambda: mgr.get_page(task_id), task_id)

    dx, dy = 0, 0
    if direction == "down":
        dy = amount
    elif direction == "up":
        dy = -amount
    elif direction == "right":
        dx = amount
    elif direction == "left":
        dx = -amount

    try:
        await page.mouse.wheel(dx, dy)
        return f"已向{direction}滚动 {amount}px。"
    except Exception as e:
        return _error(f"滚动失败: {e}")


async def _async_scroll_to(landmark, ref, selector, task_id) -> str:
    if not any([landmark, ref, selector]):
        return _error("必须指定 landmark、ref 或 selector 中的至少一个参数。")

    mgr = BrowserManager.instance()
    page = await _with_browser_recovery(lambda: mgr.get_page(task_id), task_id)

    try:
        if landmark:
            loc = page.get_by_role("region", name=landmark)
            if await loc.count() == 0:
                loc = page.locator(f"#{landmark}")
            if await loc.count() == 0:
                loc = page.locator(f"[class*='{landmark}']")
            if await loc.count() == 0:
                return _error(f"找不到 landmark={landmark} 对应的区域。")
            await loc.first.scroll_into_view_if_needed(timeout=5000)
            return f"已滚动到区域 {landmark}。"
        elif ref:
            loc = page.locator(f"[aria-ref='{ref}']")
            if await loc.count() == 0:
                return _error(
                    f"找不到 ref={ref}",
                    hint="ref 仅在当前页面视图有效。请调用 browser_snapshot() 获取新的 ref。",
                )
            await loc.first.scroll_into_view_if_needed(timeout=5000)
            return f"已滚动到 ref={ref}。"
        elif selector:
            loc = page.locator(selector)
            if await loc.count() == 0:
                return _error(f"找不到选择器 {selector} 对应的元素。")
            await loc.first.scroll_into_view_if_needed(timeout=5000)
            return f"已滚动到 {selector}。"
    except Exception as e:
        return _error(f"滚动失败: {e}")
    return ""


async def _async_wait_navigation(url_contains, timeout, task_id) -> str:
    mgr = BrowserManager.instance()
    page = await _with_browser_recovery(lambda: mgr.get_page(task_id), task_id)

    try:
        if url_contains:
            await page.wait_for_url(f"**{url_contains}**", timeout=timeout)
            return f"页面已导航至包含 \"{url_contains}\" 的 URL: {page.url}"
        else:
            await page.wait_for_load_state("load", timeout=timeout)
            return f"页面导航完成: {page.url}"
    except Exception as e:
        try:
            current_url = page.url
            title = await page.title()
        except Exception:
            current_url = "unknown"
            title = "unknown"
        return _warning(
            f"等待导航超时 ({timeout}ms): {e}",
            detail=f"当前 URL: {current_url}, 标题: {title}",
        )


# ---------------------------------------------------------------------------
# Tool functions (sync wrappers for ToolRegistry)
# ---------------------------------------------------------------------------
# ToolRegistry uses ThreadPoolExecutor to call tools synchronously.
# These wrapper functions call the async implementations via _run_async().
# task_id is injected by the framework, not visible to LLM in param_schema.


def browser_navigate(url: str, wait_until: str = "load", *, task_id: str = "") -> str:
    """导航浏览器到指定URL。

    Args:
        url: 目标URL（必填）
        wait_until: 等待策略 - load/domcontentloaded/networkidle
        task_id: 框架自动注入
    """
    return _run_async(_async_navigate(url, wait_until, task_id))


def browser_snapshot(
    scope: str | None = None,
    mode: str = "reading",
    include_offscreen: bool = False,
    wait_stable: bool = True,
    *,
    task_id: str = "",
) -> str:
    """返回当前页面的可访问性快照(Accessibility Tree)。

    Args:
        scope: CSS选择器限定区域（可选）
        mode: interactive=仅可交互元素, reading=可交互+阅读元素（默认）, full=全量
        include_offscreen: 是否包含视口外元素
        wait_stable: 是否等待DOM稳定后再快照（默认True，轮询时可设False）
        task_id: 框架自动注入
    """
    return _run_async(_async_snapshot(scope, mode, include_offscreen, task_id, wait_stable))


def browser_click(
    ref: str | None = None,
    role: str | None = None,
    name: str | None = None,
    selector: str | None = None,
    double_click: bool = False,
    pos: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """点击页面元素。

    Args:
        ref: 显示标签（不可用于定位，仅作标识）
        role: 元素角色（如 button, link）
        name: 元素名称
        selector: CSS选择器
        double_click: 是否双击
        pos: 坐标 "x,y,w,h"（从snapshot的box值复制，最可靠）
        task_id: 框架自动注入
    """
    return _run_async(_async_click(ref, role, name, selector, double_click, pos, task_id))


def browser_type(
    ref: str | None = None,
    role: str | None = None,
    name: str | None = None,
    selector: str | None = None,
    text: str = "",
    clear: bool = True,
    press_enter: bool = False,
    pos: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """在输入框中键入文本。

    Args:
        ref: 显示标签（不可用于定位）
        role: 元素角色（如 textbox, searchbox）
        name: 元素名称
        selector: CSS选择器
        text: 要输入的文本
        clear: 是否先清空输入框
        press_enter: 输入后是否按回车
        pos: 坐标 "x,y,w,h"（从snapshot的box值复制，最可靠）
        task_id: 框架自动注入
    """
    return _run_async(_async_type(ref, role, name, selector, text, clear, press_enter, pos, task_id))


def browser_read(
    selector: str | None = None,
    ref: str | None = None,
    max_chars: int = 5000,
    *,
    task_id: str = "",
) -> str:
    """阅读页面元素的文本内容。

    Args:
        selector: CSS选择器（指定区域）
        ref: 元素ref
        max_chars: 最大返回字符数（默认5000）
        task_id: 框架自动注入
    """
    return _run_async(_async_read(selector, ref, max_chars, task_id))


def browser_screenshot(
    path: str | None = None,
    full_page: bool = False,
    *,
    task_id: str = "",
) -> str:
    """截取页面截图，保存到文件，返回路径。

    Args:
        path: 保存路径（可选，默认保存到 screenshots 目录）
        full_page: 是否截取完整页面
        task_id: 框架自动注入
    """
    return _run_async(_async_screenshot(path, full_page, task_id))


def browser_evaluate(expression: str, *, task_id: str = "") -> str:
    """执行JavaScript表达式（默认禁用，需config开启）。

    Args:
        expression: JavaScript表达式
        task_id: 框架自动注入
    """
    return _run_async(_async_evaluate(expression, task_id))


def browser_wait(
    role: str | None = None,
    name: str | None = None,
    ref: str | None = None,
    text: str | None = None,
    timeout: int = 5000,
    *,
    task_id: str = "",
) -> str:
    """等待元素出现或文本出现。

    Args:
        role: 元素角色
        name: 元素名称
        ref: 元素ref
        text: 等待出现的文本
        timeout: 超时毫秒数（默认5000）
        task_id: 框架自动注入
    """
    return _run_async(_async_wait(role, name, ref, text, timeout, task_id))


def browser_scroll(
    direction: str = "down",
    amount: int = 500,
    *,
    task_id: str = "",
) -> str:
    """滚动页面。

    Args:
        direction: 滚动方向 - up/down/left/right
        amount: 滚动像素数（默认500）
        task_id: 框架自动注入
    """
    return _run_async(_async_scroll(direction, amount, task_id))


def browser_scroll_to(
    landmark: str | None = None,
    ref: str | None = None,
    selector: str | None = None,
    *,
    task_id: str = "",
) -> str:
    """滚动到指定元素。

    Args:
        landmark: 语义区域名（如 footer, search-results）
        ref: 元素ref（来自browser_snapshot）
        selector: CSS选择器
        task_id: 框架自动注入
    """
    return _run_async(_async_scroll_to(landmark, ref, selector, task_id))


def browser_wait_navigation(
    url_contains: str | None = None,
    timeout: int = 10000,
    *,
    task_id: str = "",
) -> str:
    """等待页面导航完成（click/submit触发跳转后使用）。

    Args:
        url_contains: 等待URL包含此字符串（可选）
        timeout: 超时毫秒数（默认10000）
        task_id: 框架自动注入
    """
    return _run_async(_async_wait_navigation(url_contains, timeout, task_id))


async def _async_list_pages(task_id: str) -> str:
    mgr = BrowserManager.instance()
    pages = await mgr.list_pages(task_id)
    if not pages:
        return "当前任务没有打开的页面。"

    parts = ["## 打开的标签页"]
    for p in pages:
        marker = " ← 当前" if p["active"] else ""
        parts.append(f"  [{p['index']}] \"{p['title']}\" ({p['url']}){marker}")
    return "\n".join(parts)


def browser_list_pages(*, task_id: str = "") -> str:
    """列出当前任务所有打开的标签页。

    Returns:
        格式化的标签页列表，包含索引、标题、URL和活跃状态。
    """
    return _run_async(_async_list_pages(task_id))


async def _async_switch_page(index: int, task_id: str) -> str:
    mgr = BrowserManager.instance()
    try:
        page = await mgr.switch_page(task_id, index)
    except ValueError as e:
        return _error(str(e))

    try:
        title = await page.title()
        url = page.url
    except Exception:
        title = "unknown"
        url = "unknown"

    return f"已切换到标签页 [{index}]: \"{title}\" ({url})"


def browser_switch_page(index: int, *, task_id: str = "") -> str:
    """切换到指定索引的标签页。

    Args:
        index: 标签页索引（从 browser_list_pages 获取）
        task_id: 框架自动注入
    """
    return _run_async(_async_switch_page(index, task_id))


# Note: atexit cleanup is intentionally omitted.
# BrowserManager.close_all() is async, but atexit handlers run after the event loop
# is closed, making it impossible to await async cleanup. The browser process will
# be reclaimed by the OS when the parent process exits.

// Tool-name → icon map for chat tool cards (v3 "Calm Confidence").
// Sources: agentnexus/tools/providers/* registered names; demo reference at
// designs/mockups/v3-redesign-test.html ("icon map" overlay).
// Resolution chain: exact name → first letter fallback (see ToolCard).
import {
  AlignLeft, AppWindow, ArrowRightLeft, ArrowUpDown, Ban, BookOpen, Bot, Brain,
  Camera, ClipboardCheck, Clock, Code2, Command, Compass, Copy, Download,
  FileText, FolderOpen, Globe, History, Info, Keyboard, Layers, ListChecks,
  ListPlus, ListTodo, Locate, Monitor, MousePointerClick, PenLine, Puzzle,
  Rocket, Save, Scan, Smile, SquareTerminal, Terminal, TextSearch, Timer,
  ToggleLeft, type LucideIcon,
} from 'lucide-react'

const TOOL_ICONS: Record<string, LucideIcon> = {
  // filesystem
  file_read: FileText,
  file_write: PenLine,
  file_list: FolderOpen,
  // search & knowledge
  grep_search: TextSearch,
  web_search: Globe,
  kb_search: BookOpen,
  web_fetch: Download,
  history_search: History,
  // execution
  python_execute: SquareTerminal,
  shell_exec: Terminal,
  // memory
  memory_search: Brain,
  memory_save: Save,
  memory_project_status: Info,
  // tasks & orchestration
  todo_add: ListPlus,
  todo_update: ListChecks,
  todo_list: ListTodo,
  exit_plan_mode: ClipboardCheck,
  subagent_run: Bot,
  express_reaction: Smile,
  // browser
  browser_navigate: Compass,
  browser_snapshot: Scan,
  browser_read: AlignLeft,
  browser_click: MousePointerClick,
  browser_type: Keyboard,
  browser_screenshot: Camera,
  browser_evaluate: Code2,
  browser_wait: Clock,
  browser_wait_navigation: Timer,
  browser_scroll: ArrowUpDown,
  browser_scroll_to: Locate,
  browser_dismiss_popup: Ban,
  browser_list_pages: Layers,
  browser_switch_page: Copy,
  // computer use
  computer_snapshot: Monitor,
  computer_list_windows: AppWindow,
  computer_switch_window: ArrowRightLeft,
  computer_launch: Rocket,
  computer_click: MousePointerClick,
  computer_type: Keyboard,
  computer_key: Command,
  computer_select: ArrowUpDown,
  computer_toggle: ToggleLeft,
  computer_scroll: ArrowUpDown,
  // dynamic MCP tools
  mcp_default: Puzzle,
}

/** Resolve a tool name to an icon; null → caller falls back to first letter
 *  (or the Puzzle icon for known-dynamic MCP tools). */
export function toolIcon(name: string | undefined | null): LucideIcon | null {
  if (!name) return null
  return TOOL_ICONS[name] ?? null
}

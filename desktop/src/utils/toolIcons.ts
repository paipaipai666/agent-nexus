// Tool-name → icon map for chat tool cards (v3 "Calm Confidence").
// Sources: agentnexus/tools/providers/* registered names; demo reference at
// designs/mockups/v3-redesign-test.html ("icon map" overlay).
// Resolution chain: exact name → first letter fallback (see ToolCard).
import {
  BookOpen, Bot, Brain, ClipboardCheck, Download, FileText, FolderOpen, Globe,
  History, Info, ListChecks, ListPlus, ListTodo, PenLine, Puzzle, Save, Smile,
  SquareTerminal, Terminal, TextSearch, type LucideIcon,
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
  // dynamic MCP tools
  mcp_default: Puzzle,
}

/** Resolve a tool name to an icon; null → caller falls back to first letter
 *  (or the Puzzle icon for known-dynamic MCP tools). */
export function toolIcon(name: string | undefined | null): LucideIcon | null {
  if (!name) return null
  return TOOL_ICONS[name] ?? null
}

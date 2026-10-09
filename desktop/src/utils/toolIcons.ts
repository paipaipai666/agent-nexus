import {
  BookOpen, Bot, Brain, ClipboardCheck, Download, FileText, FolderOpen, Globe,
  History, Info, ListChecks, ListPlus, ListTodo, PenLine, Puzzle, Save, Smile,
  SquareTerminal, Terminal, TextSearch, type LucideIcon,
} from 'lucide-react'

const TOOL_ICONS: Record<string, LucideIcon> = {
  file_read: FileText,
  file_write: PenLine,
  file_list: FolderOpen,
  grep_search: TextSearch,
  web_search: Globe,
  kb_search: BookOpen,
  web_fetch: Download,
  history_search: History,
  python_execute: SquareTerminal,
  shell_exec: Terminal,
  memory_search: Brain,
  memory_save: Save,
  memory_project_status: Info,
  todo_add: ListPlus,
  todo_update: ListChecks,
  todo_list: ListTodo,
  exit_plan_mode: ClipboardCheck,
  subagent_run: Bot,
  express_reaction: Smile,
  mcp_default: Puzzle,
}

export function toolIcon(name: string | undefined | null): LucideIcon | null {
  if (!name) return null
  return TOOL_ICONS[name] ?? null
}

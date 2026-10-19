"""Command entry points for desktop capabilities in the existing TUI."""

from dataclasses import dataclass
from importlib import import_module
from threading import RLock

from pydantic import ValidationError


@dataclass
class CommandResult:
    """Operation output and explicit changes to the current terminal session."""

    value: object = None
    session_id: str | None = None
    draft: str | None = None
    submission: dict | None = None


COMMAND_HELP = {
    "/session": "list [query] | new [workspace] | switch <id> | delete <id>",
    "/plan": "[status | on | off]",
    "/think": "[default | none | low | medium | high]",
    "/attach": 'list | add "<path>" | remove <number> | clear',
    "/draft": 'show | load "<UTF-8 file>" | clear | send',
    "/stop": "停止当前会话任务",
    "/todo": "查看当前会话任务列表",
    "/subagents": "list | show <id> | cancel <id> | send <id> <child_run_id> <request_id> <text> | reply <id> <child_run_id> <question_id> <request_id> <text>",
    "/timeline": "[after_id] [agent_id]",
    "/context": "<run_id> <step_id> [agent_id]",
    "/recover": 'status | save | retry | continue | export "<path>"',
    "/undo": "回退到上一检查点",
    "/redo": "恢复下一检查点",
    "/reset": "清除当前会话历史和上下文",
    "/compact": "[压缩指令]",
    "/settings": "show | set <key> <value>",
    "/providers": 'list | save "<JSON file>"',
    "/model": "list | select <provider/model> | discover <provider>",
    "/persona": 'show | save "<JSON file>"',
    "/plugin": "查看插件发现与加载报告",
    "/memory": "list [limit] | search <query> | short | delete <id> | clear | reflect [days]",
    "/kb": 'list | search <query> | ingest "<path>" | progress <run_id> | delete <id> | models | prepare [embedding | reranker]',
    "/wiki": 'list [namespace] | show <id> | stats [namespace] | query <text> | ingest "<path>" | text <text> | delete <id> | lint | reviews | resolve <id> | process | backfill',
    "/stats": "[days]",
    "/logs": "[days]",
    "/trace": "<trace_id>",
    "/health": "查看各组件健康状态",
    "/alerts": "[days] [severity] | rules",
    "/audit": "[tool] [limit]",
    "/eval": "tasks | task <id> | validate | suites | run <suite> [JSON options] | baseline <suite> | save <suite> | compare <suite>",
}

_DOMAINS = {
    **dict.fromkeys(("/session", "/plan", "/think", "/attach", "/draft", "/stop", "/todo", "/subagents", "/timeline", "/context", "/recover", "/undo", "/redo", "/reset", "/compact"), "chat"),
    **dict.fromkeys(("/settings", "/providers", "/model", "/persona", "/plugin"), "settings"),
    "/memory": "memory", "/kb": "knowledge", "/wiki": "wiki", "/eval": "evaluation",
    **dict.fromkeys(("/stats", "/logs", "/trace", "/health", "/alerts", "/audit"), "observability"),
}


def argument_path(raw: str):
    """Keep Windows backslashes and accept paths enclosed in paired quotes."""
    from pathlib import Path

    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    if not raw:
        raise ValueError("文件路径不能为空")
    return Path(raw).expanduser().resolve()


def split_action(args: str, default="list"):
    """Separate a subcommand from its untouched text or path argument."""
    parts = args.strip().split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else "") if parts else (default, "")


class TerminalCommands:
    """Run domain operations without importing Textual or HTTP adapters."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.attachments = {}
        self.drafts = {}
        self.submissions = {}
        self.last_ingestion = {}
        self.inputs = {}
        self.collaboration_drafts = {}
        self.collaboration_requests = {}
        self._loaded_drafts = set()
        from agentnexus.tui.drafts import DraftStore

        self._draft_lock = RLock()
        self._draft_store = DraftStore(lock=self._draft_lock)

    def load_draft(self, sid):
        """Restore a conversation's pending input and attachments once."""
        with self._draft_lock:
            if sid not in self._loaded_drafts:
                saved = self._draft_store.load(sid)
                self.inputs.setdefault(sid, saved["input"])
                self.drafts.setdefault(sid, saved["draft"])
                self.attachments.setdefault(sid, saved["attachments"])
                self._loaded_drafts.add(sid)

    def save_draft(self, sid, *, text=None):
        """Persist pending input without retaining slash commands."""
        with self._draft_lock:
            self.load_draft(sid)
            text = text if text is not None else self.inputs.get(sid, "")
            self.inputs[sid] = text if not text.startswith("/") else ""
            self._draft_store.save(sid, {"input": self.inputs.get(sid, ""), "draft": self.drafts.get(sid, ""), "attachments": self.attachments.get(sid, [])})

    def requires_confirmation(self, command, args):
        action, _ = split_action(args)
        return command == "/reset" or (command, action) in {
            ("/session", "delete"), ("/memory", "delete"), ("/memory", "clear"),
            ("/memory", "reflect"), ("/kb", "delete"), ("/kb", "prepare"),
            ("/kb", "retry"), ("/kb", "rollback"),
            ("/wiki", "delete"), ("/wiki", "backfill"), ("/wiki", "process"),
            ("/eval", "run"), ("/eval", "save"), ("/eval", "compare"),
        }

    def run(self, command: str, args: str, session_id: str) -> CommandResult:
        """Execute a documented terminal command for an explicit session."""
        if command not in _DOMAINS:
            raise ValueError(f"未知命令: {command}")
        self.load_draft(session_id)
        module = import_module(f"agentnexus.tui.commands.{_DOMAINS[command]}")
        try:
            result = module.run(self, command, args.strip(), session_id)
        except ValidationError as exc:
            details = "; ".join(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors(include_input=False, include_context=False, include_url=False))
            raise ValueError(f"配置校验失败: {details}") from None
        if command in {"/attach", "/draft", "/recover"}:
            self.save_draft(session_id)
        return result if isinstance(result, CommandResult) else CommandResult(result)

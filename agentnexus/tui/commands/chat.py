"""Session, execution, attachment, collaboration and recovery commands."""

import json
import mimetypes
from dataclasses import asdict

from . import COMMAND_HELP, CommandResult, argument_path, split_action


def run(context, command, args, sid):
    """Apply a chat operation to its explicit session."""
    chat = context.runtime.chat
    if command == "/plan":
        if args in {"", "status"}:
            return {"plan_mode": chat.is_plan_mode(sid)}
        if args in {"on", "off"}:
            return {"plan_mode": chat.set_plan_mode(sid, args == "on")}
    elif command == "/think":
        effort = chat.get_thinking_effort(sid) if not args else chat.set_thinking_effort(sid, None if args == "default" else args)
        return {"thinking_effort": effort}
    elif command == "/attach":
        action, rest = split_action(args)
        items = context.attachments.setdefault(sid, [])
        if action == "list":
            return list(items)
        if action == "clear":
            items.clear()
            return []
        if action == "add":
            path = argument_path(rest)
            if not path.is_file():
                raise ValueError(f"附件不存在: {path}")
            if len(items) >= 10:
                raise ValueError("最多添加 10 个附件")
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            size = path.stat().st_size
            if mime.startswith("image/") and size > 5 * 1024 * 1024:
                raise ValueError("图片不能超过 5 MB")
            if not any(item["path"] == str(path) for item in items):
                items.append({"path": str(path), "name": path.name, "mime": mime, "size": size})
            return list(items)
        if action == "remove" and rest.isdigit() and 1 <= int(rest) <= len(items):
            items.pop(int(rest) - 1)
            return list(items)
    elif command == "/draft":
        action, rest = split_action(args, "show")
        if action == "load":
            context.drafts[sid] = argument_path(rest).read_text(encoding="utf-8")
        elif action == "clear":
            context.drafts.pop(sid, None)
        elif action == "send":
            text = context.drafts.get(sid, "")
            if not text.strip():
                raise ValueError("草稿为空")
            attachments = [dict(item) for item in context.attachments.pop(sid, [])]
            return CommandResult({"status": "提交草稿"}, submission={"text": text, "attachments": attachments or None})
        elif action != "show":
            raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
        return {"draft": context.drafts.get(sid, "")}
    elif command == "/session":
        return _session(context, args, sid)
    elif command == "/stop":
        recovery = chat.get_session_recovery(sid)
        if not recovery or not recovery.get("execution_active"):
            return {"status": "当前没有执行中的任务"}
        chat.cancel_run(recovery["run_id"])
        return {"status": "已请求停止", "run_id": recovery["run_id"]}
    elif command == "/todo":
        todo = getattr(chat.session_agent(sid), "_todo_list", None)
        return [asdict(item) for item in todo.list_items()] if todo is not None else []
    elif command == "/subagents":
        return _subagents(chat, sid, args)
    elif command == "/timeline":
        from agentnexus.observability.timeline import get_timeline_store

        parts = args.split()
        return get_timeline_store().list_events(sid, after_id=int(parts[0]) if parts else 0, limit=2000, agent_id=parts[1] if len(parts) > 1 else None, normalize=True)
    elif command == "/context":
        from agentnexus.observability.timeline import get_timeline_store

        parts = args.split()
        if len(parts) in {2, 3}:
            return get_timeline_store().get_snapshot(sid, parts[0], int(parts[1]), agent_id=parts[2] if len(parts) == 3 else None)
    elif command == "/recover":
        return _recovery(context, sid, args)
    elif command in {"/undo", "/redo", "/reset", "/compact"}:
        if chat.is_session_processing(sid):
            raise ValueError("会话仍在执行，请先停止任务并等待执行结束")
        version = chat.session_version(sid)
        if command == "/compact":
            memory = chat.session_memory(sid)
            return {"status": "compacted", "tokens_saved": memory.maybe_compact(threshold=None, custom_instructions=args, is_auto=False)}
        if command == "/reset":
            version.reset()
            chat.restore_checkpoint_memory(sid)
            return {"status": "reset"}
        checkpoint = version.undo() if command == "/undo" else version.redo()
        if checkpoint is not None:
            chat.restore_checkpoint_memory(sid)
        return {"status": "restored" if checkpoint is not None else "unchanged", "checkpoint": checkpoint}
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")


def _session(context, args, sid):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    chat = context.runtime.chat
    action, rest = split_action(args)
    if action == "list":
        sessions = ConversationVersionManager.find_recent_sessions(get_settings().memory_db_path, None, limit=100)
        return [s for s in sessions if not rest or rest.lower() in str(s).lower()]
    if action == "new":
        workspace = argument_path(rest) if rest else argument_path(chat.get_session_workspace(sid) or ".")
        if not workspace.is_dir():
            raise ValueError(f"工作区目录不存在: {workspace}")
        handle = chat.start_session(profile="tui", workspace=str(workspace))
        chat.session_version(handle.id)
        return CommandResult({"session_id": handle.id, "workspace": handle.workspace}, session_id=handle.id)
    if action == "switch":
        restored = chat.ensure_session_restored(rest)
        try:
            chat.get_session_snapshot(rest)
        except KeyError:
            if not restored:
                raise ValueError(f"会话不存在: {rest}")
        return CommandResult({"session_id": rest}, session_id=rest)
    if action == "delete":
        if rest == sid:
            raise ValueError("请先切换到其他会话，再删除当前会话")
        if chat.is_session_processing(rest):
            raise ValueError("会话仍在执行，请先停止任务")
        if not rest or (rest not in chat._sessions and not chat.ensure_session_restored(rest)):
            raise ValueError(f"会话不存在: {rest}")
        chat.delete_session(rest)
        chat._sessions.pop(rest, None)
        chat._version_managers.pop(rest, None)
        ConversationVersionManager.delete_session(get_settings().memory_db_path, rest)
        context.attachments.pop(rest, None)
        context.drafts.pop(rest, None)
        context.submissions.pop(rest, None)
        context.inputs.pop(rest, None)
        context.save_draft(rest)
        return {"deleted": rest}
    raise ValueError(f"用法: /session {COMMAND_HELP['/session']}")


def _subagents(chat, sid, args):
    from agentnexus.observability.timeline import get_timeline_store

    action, rest = split_action(args)
    if action == "list":
        return chat.list_subagents(sid)
    if action == "show":
        summary = next((item for item in chat.list_subagents(sid) if item["subagent_id"] == rest), None)
        if summary is None:
            raise ValueError("子代理不属于当前会话")
        return {"subagent": summary, "messages": get_timeline_store().list_collaboration_messages(sid, rest),
                "events": get_timeline_store().list_events(sid, limit=2000, agent_id=rest, normalize=True)}
    if action == "cancel":
        return {"accepted": chat.cancel_subagent(sid, rest), "subagent_id": rest}
    if action in {"send", "reply"}:
        parts = rest.split(maxsplit=4 if action == "reply" else 3)
        if len(parts) == (5 if action == "reply" else 4):
            child_id, child_run = parts[:2]
            question = int(parts[2]) if action == "reply" else None
            request_id, content = parts[-2:]
            if not content.strip() or len(content) > 16000 or not 1 <= len(request_id) <= 128 or (question is not None and question < 1):
                raise ValueError("消息、请求 ID 或问题 ID 无效")
            return chat.send_subagent_message(sid, child_id, content, request_id, child_run, question)
    raise ValueError(f"用法: /subagents {COMMAND_HELP['/subagents']}")


def _recovery(context, sid, args):
    chat = context.runtime.chat
    action, rest = split_action(args, "status")
    original = context.submissions.get(sid)
    recovery = chat.get_session_recovery(sid)
    if action == "status":
        return {"recovery": recovery, "original_submission": original}
    if action == "retry" and original:
        receipt = chat.get_submission_receipt(sid, original["client_message_id"])
        return receipt if receipt else CommandResult({"status": "重试原提交"}, submission=dict(original))
    if not recovery:
        raise ValueError("没有可恢复的任务")
    if action == "save":
        return chat.retry_run_save(recovery["run_id"])
    if action == "export":
        path = argument_path(rest)
        with path.open("x", encoding="utf-8") as file:
            json.dump(recovery, file, ensure_ascii=False, indent=2)
        return {"path": str(path)}
    if action == "continue":
        if recovery.get("execution_active"):
            raise ValueError("任务尚未停止，请等待执行结束")
        draft = f"请先核对上一轮已完成的操作和文件修改，再从未完成的步骤继续，避免重复执行。原请求：{recovery['question']}"
        context.drafts[sid] = draft
        return CommandResult({"status": "继续请求已放入草稿，请检查后发送"}, draft=draft)
    raise ValueError(f"用法: /recover {COMMAND_HELP['/recover']}")

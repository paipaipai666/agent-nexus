"""Textual adapter for the shared chat execution and event stream."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.widgets import Static

from agentnexus.tools.result_format import (
    condense_fetch_result,
    condense_file_result,
    condense_search_result,
    extract_diff_parts,
    format_subagent_result,
    summarize_tool_result,
)
from agentnexus.tui.activity import RunActivity
from agentnexus.tui.widgets.confirm_dialog import ConfirmDialog
from agentnexus.tui.widgets.message import ChatMessage, ReasoningBlock, ToolCall, render_diff_with_colors

logger = logging.getLogger(__name__)


class ChatExecution:
    """Render shared service events without owning agent execution or persistence."""

    def _submit_session_message(self, submission, sid=None):
        sid = sid or self._chat_session.id
        submission = dict(submission)
        self._terminal.save_draft(sid)
        submission["attachments"] = [dict(item) for item in submission.get("attachments") or []] or None
        if sid in self._running_sessions:
            self._queued_messages.setdefault(sid, []).append(submission)
        else:
            self._running_sessions.add(sid)
            self._run_agent(session_id=sid, **submission)

    @work(group="chat-execution")
    async def _run_agent(self, text: str, *, session_id=None, attachments=None, client_message_id=None, skill_id=None, session_profile=None):
        sid = session_id or self._chat_session.id
        submission = {"text": text, "attachments": attachments, "client_message_id": client_message_id or uuid.uuid4().hex}
        if skill_id is not None:
            submission["skill_id"] = skill_id
        if session_profile is not None:
            submission["session_profile"] = session_profile
        self._submissions[sid] = submission
        self._run_transcripts[sid] = []
        self._running_sessions.add(sid)
        self._activity_runs[sid] = RunActivity()
        if sid == self._chat_session.id:
            self._refresh_workspace()
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()
        started = []

        def on_started(run):
            started.append(run)
            loop.call_soon_threadsafe(ready.set)

        def execute():
            agent = self._chat_service.session_agent(sid)
            if skill_id is not None:
                skill = self._chat_service.session_skills(sid)
                if skill is not None:
                    skill.use(skill_id)
                elif session_profile is not None:
                    agent.set_session_profile(session_profile)
            has_confirm_handler = hasattr(agent, "confirm_handler")
            old_confirm = getattr(agent, "confirm_handler", None)
            bridge = getattr(self.app, "_subagent_confirm", None)
            thread_id = threading.get_ident()
            def confirm(summary):
                return self._confirm_execution(sid, started, summary)
            if has_confirm_handler:
                agent.confirm_handler = confirm
            if bridge is not None:
                bridge.set_target(confirm, thread_id=thread_id)
            try:
                return self._chat_service.send_message(sid, text, on_run_started=on_started, **{
                    "attachments": submission["attachments"], "client_message_id": submission["client_message_id"],
                })
            finally:
                if has_confirm_handler:
                    agent.confirm_handler = old_confirm
                if bridge is not None:
                    bridge.set_target(None, thread_id=thread_id)

        def consume(run):
            for event in self._chat_service.stream_events(run.id, timeout=None):
                if self._closing:
                    return
                self.app.call_from_thread(self._apply_service_event, event)

        running = asyncio.create_task(asyncio.to_thread(execute))
        waiting = asyncio.create_task(ready.wait())
        try:
            await asyncio.wait((running, waiting), return_when=asyncio.FIRST_COMPLETED)
            if started:
                run = started[0]
                self._activity_runs[sid].run_id = run.id
                self._active_runs[sid] = run.id
                if sid == self._chat_session.id:
                    self._current_run_id = run.id
                    self._streaming_msg_widget = None
                    self._streaming_buffer = ""
                    self._reasoning_widget = None
                    self._refresh_workspace()
                results = await asyncio.gather(running, asyncio.to_thread(consume, run), return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.debug("TUI execution settled with %s", result)
            else:
                await running
        except asyncio.CancelledError:
            if started:
                self._chat_service.cancel_run(started[0].id, reason="tui_closed")
            raise
        except Exception as exc:
            if sid == self._chat_session.id and not self._closing:
                self._chat_area.add_message("system", f"请求未能开始: {exc}。原请求已保留，可用 /recover 查看。")
        finally:
            waiting.cancel()
            self._running_sessions.discard(sid)
            self._activity_runs.pop(sid, None)
            self._run_transcripts.pop(sid, None)
            if not self._closing:
                if sid == self._chat_session.id:
                    self._remove_loading()
                    self._refresh_version_display()
                    self._refresh_todo_panel()
                    self._refresh_skill_panel()
                    usage = getattr(self._agent, "total_usage", {})
                    usage = usage if isinstance(usage, dict) else {}
                    self._hud.update_context(current_tokens=self._memory.estimate_stm_tokens() if self._memory else 0,
                                             total_input=usage.get("input_tokens", 0), total_output=usage.get("output_tokens", 0))
                self._drain_session_queue(sid)
                if sid == self._chat_session.id:
                    self._refresh_workspace()

    def _confirm_execution(self, sid, started, summary):
        result = [False]
        completed = threading.Event()
        dialog = ConfirmDialog(f"会话 {sid}", summary, risk_level="high")

        def done(approved):
            self._set_confirmation_activity(sid, False)
            result[0] = bool(approved)
            completed.set()

        def show_confirmation():
            self._set_confirmation_activity(sid, True)
            self.app.push_screen(dialog, callback=done)

        self.app.call_from_thread(show_confirmation)
        while not completed.wait(0.05):
            if self._closing or (started and not self._chat_service.is_run_active(started[0].id)):
                self.app.call_from_thread(lambda: dialog.dismiss(False) if self.app.screen is dialog else None)
                return False
        return result[0]

    def _set_confirmation_activity(self, sid, active):
        progress = self._activity_runs.get(sid)
        if progress is not None:
            progress.confirming = active
        if sid == self._chat_session.id:
            self._refresh_workspace()

    def _remove_loading(self):
        self._stop_spinner()

    def _finish_reasoning(self):
        if self._reasoning_widget is not None:
            self._reasoning_widget.finish()
            self._reasoning_widget = None

    def _finish_streaming_message(self):
        if self._streaming_msg_widget is not None:
            widget = self._streaming_msg_widget
            widget.content = self._streaming_buffer
            self.call_after_refresh(lambda: widget.query_one("#msg-content", Static).update(Markdown(widget.content, code_theme="ansi_dark")) if widget.is_mounted else None)
            self._streaming_msg_widget = None
            self._streaming_buffer = ""

    def _apply_service_event(self, event, *, replay=False):
        if not replay and event.session_id in self._activity_runs:
            self._activity_runs[event.session_id].observe(event)
        if not replay and event.session_id in self._running_sessions:
            self._run_transcripts.setdefault(event.session_id, []).append(event)
        if event.session_id != self._chat_session.id or self._closing:
            return
        payload = event.payload
        kind = event.type
        if not replay and kind in {"stream_token", "stream_reasoning"}:
            self._refresh_activity()
        if kind == "stream_token":
            self._finish_reasoning()
            if self._streaming_msg_widget is None:
                self._streaming_msg_widget = ChatMessage("assistant", "")
                self._chat_area.mount(self._streaming_msg_widget)
                self._streaming_buffer = ""
            self._streaming_buffer += payload.get("token", "")
            widget, content = self._streaming_msg_widget, self._streaming_buffer
            self.call_after_refresh(lambda: widget.query_one("#msg-content", Static).update(Text(content)) if widget.is_mounted else None)
        elif kind == "tool_start":
            self._finish_reasoning()
            name = payload.get("name", "tool")
            if name == "express_reaction":
                from agentnexus.tools.user_reaction import REACTION_EMOJI, parse_reaction_arguments

                reaction, comment = parse_reaction_arguments(payload.get("arguments", {}))
                if self._last_user_msg_widget is not None:
                    self._last_user_msg_widget.attach_reaction(REACTION_EMOJI.get(reaction, ""), comment)
                return
            self._finish_streaming_message()
            arguments = payload.get("arguments", {})
            detail = ""
            if isinstance(arguments, dict):
                detail = str(next((arguments[key] for key in ("path", "file_path", "query", "url", "namespace") if arguments.get(key)), "")).replace("\n", " ")
            widget = ToolCall(name, "executing...", detail=detail)
            self._tool_widgets[(event.run_id, payload.get("id") or name)] = widget
            self._chat_area.mount(widget)
            self._turn_tool_names.append(name)
        elif kind == "tool_done":
            name = payload.get("name", "tool")
            widget = self._tool_widgets.pop((event.run_id, payload.get("id") or name), None)
            if widget is not None:
                result = str(payload.get("raw_result", payload.get("result", "")))
                widget.raw_result = result
                raw_result = result
                formatter = {"web_search": condense_search_result, "web_fetch": condense_fetch_result,
                             "file_read": condense_file_result, "subagent_run": format_subagent_result}.get(name.strip().lower())
                markup = False
                if formatter is not None:
                    result = formatter(result)
                else:
                    diff = extract_diff_parts(result)
                    if diff:
                        title, text = diff
                        result = f"{title}\n\n[dim]Diff preview:[/]\n{render_diff_with_colors(text)}"
                        markup = True
                    else:
                        result = summarize_tool_result(result)
                duration = payload.get("duration_ms", 0)
                widget.result, widget.duration_ms = result, duration
                if widget.is_mounted:
                    widget.update_result(result, duration, markup=markup, raw_result=raw_result)
                else:
                    self.call_after_refresh(lambda: widget.update_result(result, duration, markup=markup, raw_result=raw_result) if widget.is_mounted else None)
        elif kind == "turn_journal":
            thought = payload.get("thought")
            if thought:
                self._finish_reasoning()
                self._finish_streaming_message()
                self._chat_area.mount(ReasoningBlock(str(thought)))
                self._turn_thought_count += 1
            elif payload.get("event") in {"DEGRADED", "RETRIES_LEFT", "FAULT"}:
                self._chat_area.add_message("system", str(payload.get("reason") or payload.get("strategy") or payload.get("event")))
        elif kind == "stream_reasoning":
            token = payload.get("token", "")
            if token:
                if self._reasoning_widget is None:
                    self._finish_streaming_message()
                    self._reasoning_widget = ReasoningBlock(active=True)
                    self._chat_area.mount(self._reasoning_widget)
                    self._turn_thought_count += 1
                self._reasoning_widget.append_token(token)
        elif kind in {"workflow_step", "skill_auto_selected"}:
            self._side_panel.add_timeline_event("run", str(payload.get("summary") or payload.get("skill") or payload.get("status") or kind))
        elif kind == "subagent_event":
            state = payload.get("kind", "")
            if state not in {"token", "reasoning", "stream_token", "stream_reasoning"}:
                child = payload.get("subagent_id", "")
                messages = payload.get("messages", []) if state == "communication" else []
                for message in messages:
                    delivery = message.get("delivery", message.get("delivery_state", ""))
                    identity = (event.session_id, child, message.get("id"), message.get("revision"), delivery)
                    if identity in self._seen_collaboration_messages:
                        continue
                    self._seen_collaboration_messages.add(identity)
                    if message.get("kind") in {"question", "result", "reply"}:
                        self._chat_area.add_message("system", f"子代理 {child} {message.get('kind', '')} #{message.get('id', '')} ({delivery}): {message.get('content', '')}")
                if not messages:
                    self._side_panel.add_timeline_event("run", f"{child}: {state} {payload.get('status', '')}")
        elif kind == "todo_change":
            self._refresh_todo_panel()
        elif kind in {"run_finished", "run_failed", "run_interrupted"}:
            self._finish_reasoning()
            self._remove_loading()
            answer = payload.get("answer", "")
            if self._streaming_msg_widget is not None:
                widget = self._streaming_msg_widget
                self._streaming_msg_widget = None
                widget.content = answer
                self.call_after_refresh(lambda: widget.query_one("#msg-content", Static).update(Markdown(answer, code_theme="ansi_dark")) if widget.is_mounted else None)
            elif answer:
                self._chat_area.add_message("assistant", answer)
            if kind != "run_finished" or payload.get("error"):
                self._chat_area.add_message("system", str(payload.get("reason") or payload.get("error") or kind))
            self._record_turn_summary(payload.get("question", ""), answer)
        elif kind == "run_save_failed":
            self._chat_area.add_message("system", f"结果尚未保存: {payload.get('save_error', '')}。使用 /recover save 重试保存。")
        self._chat_area.follow_output()
        if kind not in {"stream_token", "stream_reasoning"} and not replay:
            self._refresh_workspace()

    def _drain_session_queue(self, sid):
        queued = self._queued_messages.get(sid, [])
        if queued:
            submission = queued.pop(0)
            self._submit_session_message(submission, sid)

    def on_unmount(self):
        self._closing = True
        for run_id in self._active_runs.values():
            if self._chat_service.is_run_active(run_id):
                self._chat_service.cancel_run(run_id, reason="tui_closed")

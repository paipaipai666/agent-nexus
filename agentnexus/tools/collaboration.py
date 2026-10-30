"""Tool contracts for asynchronous parent-child collaboration."""

import json
import uuid

from agentnexus.tools.registry import ToolMeta


def register_parent_collaboration(executor, context, task_schema):
    """Expose distinct async tools only when a desktop run owns a coordinator."""
    def active_run():
        owner = executor.subagent_bridge.get_context()
        if owner is None or owner.collaboration is None:
            raise ValueError("当前运行不支持子代理协作")
        return owner.collaboration

    def start(task, role="explorer", allowed_tools=None, name=None):
        from agentnexus.tools.subagent import _normalize_role, _resolve_allowed_tools, _run_subagent_attempt

        run = active_run()
        role = _normalize_role(role)
        tools, _ = _resolve_allowed_tools(role, allowed_tools, context.mcp_manager)
        if not tools or not task.strip():
            raise ValueError("需要明确的子任务与安全工具")

        def execute(mailbox):
            result, error = _run_subagent_attempt(
                context.llm_client, context.non_interactive, task, role, tools,
                subagent_confirm=context.subagent_confirm, mcp_manager=context.mcp_manager,
                cancel_bridge=executor.cancel_bridge, subagent_ctx=run.context,
                subagent_entry=mailbox.child.entry, mailbox=mailbox,
                modules=context.modules,
            )
            if error is not None:
                raise error
            return result

        return json.dumps(run.start(task, role, name, execute), ensure_ascii=False)

    def send(subagent_id, content, request_id=None, question_id=None):
        return json.dumps(active_run().send(subagent_id, content, request_id or uuid.uuid4().hex, question_id), ensure_ascii=False)

    def wait(timeout=30):
        return json.dumps(active_run().wait(timeout), ensure_ascii=False)

    definitions = [
        ("subagent_start", "启动后台子任务并立即返回身份。主代理可以继续工作，子代理可汇报和提问；用 subagent_wait 等待结果。[不适用] 简单单步任务(直接用具体工具), 需要本轮同步拿到结果才能继续的任务(用subagent_run)。", task_schema, start, 30),
        ("subagent_send", "向本运行的子代理追加指令，或用 question_id 回答其问题。request_id 用于重试幂等。[不适用] 首次发起子任务(用subagent_start), 目标子代理已结束或不存在。", {
            "type": "object", "properties": {"subagent_id": {"type": "string"}, "content": {"type": "string", "maxLength": 16000}, "question_id": {"type": "integer", "minimum": 1}, "request_id": {"type": "string", "maxLength": 128}}, "required": ["subagent_id", "content"]}, send, 30),
        ("subagent_wait", "等待子代理的新消息或完成状态，返回后下一轮会自动读入消息。等待不调用模型。[不适用] 尚无运行中的子代理, 或等待期间仍有其他工作可推进(子代理完成会自动汇报)。", {
            "type": "object", "properties": {"timeout": {"type": "number", "minimum": 0, "maximum": 60}}}, wait, 65),
    ]
    for name, description, schema, function, timeout in definitions:
        if context.want(name) or context.include_tools is None:
            executor.register(ToolMeta(name=name, description=description, param_schema=schema, timeout_sec=timeout,
                                       requires_subagent_context=True, read_only=name != "subagent_start"), function)


def register_child_collaboration(executor, mailbox):
    """Child senders are derived from execution ownership, never model arguments."""
    for name, description, function, timeout in [
        ("subagent_report", "向主代理汇报阶段进展，返回持久化回执。", mailbox.report, 30),
        ("subagent_ask", "向主代理提问并等待答复。收到追加指令时也会返回，但原问题仍有效。", mailbox.ask, 125),
    ]:
        properties = {"content": {"type": "string", "maxLength": 16000}, "request_id": {"type": "string", "maxLength": 128}}
        if name == "subagent_ask":
            properties["timeout"] = {"type": "number", "minimum": 0, "maximum": 120}
        executor.register(ToolMeta(name=name, description=description, param_schema={"type": "object", "properties": properties, "required": ["content", "request_id"]}, timeout_sec=timeout, read_only=True), function)

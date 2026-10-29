"""User actions for failures at provider, configuration, and storage boundaries."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FailureAdvice:
    code: str
    message: str
    action: str


def failure_advice(error: Exception | str) -> FailureAdvice:
    """Classify known boundary failures without retrying task side effects."""
    text = str(error).lower()
    status = getattr(error, "status_code", None)
    if status in (401, 403) or re.search(r"\b(401|403)\b|invalid api key|unauthorized|authentication", text):
        return FailureAdvice("provider_auth", "模型提供商拒绝了访问凭据", "打开提供商设置检查密钥与权限，再核对已执行步骤。")
    if status == 429 or re.search(r"\b429\b|rate.?limit|quota|配额|限流", text):
        return FailureAdvice("provider_rate_limit", "模型请求受限或额度不足", "稍后重试或切换可用模型，继续前核对已执行步骤。")
    if isinstance(error, TimeoutError) or re.search(r"timeout|timed out|超时", text):
        return FailureAdvice("provider_timeout", "模型或工具请求超时", "检查连接并核对已执行步骤，确认没有重复操作后准备继续请求。")
    if isinstance(error, ConnectionError) or re.search(r"connection|connecterror|network|连接|网络", text):
        return FailureAdvice("connection_error", "连接失败，任务未能继续", "恢复连接并查询任务状态，再核对已完成的操作。")
    if isinstance(error, OSError) or re.search(r"disk|database|memorycommit|存储|磁盘", text):
        return FailureAdvice("storage_error", "结果存储失败", "检查磁盘空间与文件权限，保留结果副本并重试保存。")
    return FailureAdvice("execution_error", "任务执行失败", "查看恢复记录与时间线，核对已完成的操作后准备继续请求。")

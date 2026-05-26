# ⚡ 代码执行与安全

## 沙箱降级链

`python_execute` 和 `shell_exec` 共享相同的后端选择逻辑：

```
code_execution_backend (默认 "auto")
    │
    ├── "disabled" → [blocked]
    │
    ├── "e2b" → E2B 云端（需 AGENTNEXUS_E2B_API_KEY）
    │
    ├── "native" → OS 原生
    │     Linux: bubblewrap (bwrap --seccomp --ro-bind)
    │     macOS: sandbox-exec / Seatbelt
    │     Windows: 不可用，降级
    │
    ├── "docker" → docker run --rm
    │     --network none --cpus 1 --memory 256m
    │     --read-only --security-opt=no-new-privileges
    │
    └── "local_unsafe" → sys.executable -c <code>
          (需 code_execution_allow_unsafe_local=true)
```

`auto` 模式按 E2B → Native → Docker → 本地兜底（强警告）顺序尝试。

## Shell 三层黑名单

1. **通用**：`shutdown`, `rm -rf /`, 编码混淆 PowerShell
2. **平台特定**：Windows 禁止 `format`/`diskpart`/`reg add`；Unix 禁止 `mkfs`/`dd`/`chmod 777 /`/fork 炸弹
3. **用户自定义**：`shell_blacklist` 配置，NFKC 归一化防 Unicode 绕过

## 子代理委派

`subagent_run` 创建隔离的 Agent-in-Agent：

| 角色 | 可用工具 |
|------|----------|
| **explorer**（统一映射） | `web_search`, `grep_search`, `kb_search`, `file_read`, `file_list`, `memory_search`, `python_execute` |
| **executor** | `python_execute`, `file_read`, `file_list`, `grep_search` |

- 克隆父 Agent 的 LLM 配置
- 首次失败后以 explorer 角色 +1 max_steps 重试
- 返回 `{"status", "role", "answer", "steps_used", "allowed_tools"}`

## MCP 安全

- 健康检查：后台协程定时 ping，指数退避重连
- 子代理隔离：`allowed_agents` 控制 MCP 工具暴露范围
- 全量治理：MCP 工具自动享受 Registry 全部 7 道关卡

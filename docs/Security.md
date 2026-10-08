> **[中文](Security.md) | [English](Security.en.md)**

# 🔒 安全模型

## 工具安全 (7 道关卡)

所有工具必经 [ToolRegistry](Tool-Governance.md) 的 RBAC → Schema → 限流 → 超时 → 风险 → HITL → 审计。

## 代码执行安全（shell_exec）

- `shell_exec` 沙箱降级链：bubblewrap (Linux) / Seatbelt (macOS) / Low-IL 受限令牌+Job Object (Windows) → Docker（含 daemon 探测，daemon 不可用会继续降级） → 本地警告兜底
- Shell 三层黑名单（通用 + 平台 + 用户自定义）
- NFKC 归一化防 Unicode 绕过

## 路径沙箱（file 工具）

`file_read` / `file_write` / `file_list` 的路径经 `_resolve_safe` 两层校验（normpath 挡 `..` 穿越、resolve 挡符号链接逃逸），允许根 = 会话 workspace + `~/.agentnexus` + 包目录 + 本轮附件 + **用户批准集** + config `allowed_paths`。

越界时的放行通道（两道，均为显式同意）：

1. **会话级提示放行**：tool_runner 调用前预检越界，经 HITL 确认通道问用户一次——批准则该路径加入按 workspace 作用域的会话级批准集（进程存活期有效，并发会话不串），并顺带覆盖 file_write 的 HITL 门；拒绝则工具不执行（`HITL_BLOCKED`）。无确认通道（非交互）保持硬错误。
2. **永久列表**：config.yaml `allowed_paths`（目录或文件路径列表）。刻意**不进** config API 白名单——放宽沙箱与 `shell_blacklist` 同级信任类，只能本地编辑。

`shell_exec` 的 `cwd` 同样过 `_resolve_safe`，但越界无提示通道（保持硬错误）。

## PII 脱敏

`MemoryManager._mask_pii()` 实现部分脱敏，所有写入 LTM 的文本经过此函数：

| 类型 | 原始 | 脱敏后 |
|------|------|--------|
| 邮箱 | `user@example.com` | `u***@***.com` |
| 手机 | `13812345678` | `138****5678` |
| API Key | `sk-xxx...` | 保留 `sk-` 前缀 |
| 信用卡 | `1234567890123456` | `1234****3456` |

## 密钥处理

- 配置中密钥字段类型为 `SecretStr`
- 审计日志 `_truncate_params()` 自动脱敏
- Trace 输入输出截断 5000 字符

## MCP 安全

- 健康检查 + 指数退避重连
- `allowed_agents` 控制暴露范围
- 导入工具自动享受全量 7 道治理关卡

## 子代理隔离

| 角色 | 可用工具 |
|------|----------|
| explorer | 只读 + 搜索 + 代码执行 |
| executor | 代码 + 文件操作 |

## 相关测试

`tests/security/` 包含：沙箱逃逸、路径穿越、数据注入、间接注入、MCP 安全、密钥泄露、权限提升、工具隔离。

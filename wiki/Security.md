# 🔒 安全模型

## 工具安全 (7 道关卡)

所有工具必经 [ToolRegistry](Tool-Governance.md) 的 RBAC → Schema → 限流 → 超时 → 风险 → HITL → 审计。

## 代码执行安全

- E2B → bubblewrap/Seatbelt → Docker → 本地兜底（[详见图](Code-Execution.md)）
- Shell 三层黑名单（通用 + 平台 + 用户自定义）
- NFKC 归一化防 Unicode 绕过

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

# XRuntime P2 企业运行时 — 模块文档

> 版本: v0.1.0
> 阶段: P2 企业运行时 (W6-W8)
> 日期: 2026-06-23

## 概述

P2 阶段完成了企业级运行时能力：审计日志、配额控制、RBAC 权限、
敏感数据脱敏、多租户隔离、迁移框架。共新增 61 项测试，全部通过。

累计 171 项测试通过 (42 P0 + 68 P1 + 61 P2)。

## 模块清单

### 1. AuditMiddleware — 审计日志

**文件**: `src/xruntime/_runtime/_middleware/_audit.py`

**职责**: 记录每次工具调用的完整审计轨迹（who/what/decision/result/duration）。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `AuditEntry` | 审计条目 (timestamp/tenant/user/session/tool/input/decision/result/duration) |
| `AuditLogger` | 日志 sink (memory/file-JSONL) |
| `AuditMiddleware` | on_acting 钩子，自动捕获工具调用并写入审计 |

**测试**: 6 项

---

### 2. QuotaMiddleware — 配额控制

**文件**: `src/xruntime/_runtime/_middleware/_quota.py`

**职责**: 按会话/租户级强制执行 token / 工具调用次数 / USD 成本上限。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `QuotaConfig` | 配额限制 (max_tokens/max_tool_calls/max_cost_usd, None=无限) |
| `QuotaTracker` | 用量追踪器 (consume_tokens/tool_calls/cost) |
| `QuotaExceededError` | 超额异常 (含 limit_type/current/limit) |
| `QuotaMiddleware` | on_acting 钩子，调用前检查工具调用配额 |

**测试**: 12 项

---

### 3. RbacMiddleware — 角色权限

**文件**: `src/xruntime/_runtime/_middleware/_rbac.py`

**职责**: 在 AS PermissionEngine 之上叠加 RBAC 角色→工具权限映射。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `RbacRule` | 规则 (tool_pattern 支持 glob + action allow/deny) |
| `RoleDefinition` | 角色 (name + rules, 首匹配优先, 默认 deny) |
| `RbacMiddleware` | 会话→角色分配 + 工具检查 |

**规则匹配**:
- 精确匹配: `RbacRule("Read", "allow")` → 只允许 Read
- Glob 匹配: `RbacRule("mcp__github__*", "allow")` → 允许所有 github MCP 工具
- 默认拒绝: 未匹配任何规则的工具 → deny

**测试**: 11 项

---

### 4. SecretRedactionMiddleware — 敏感数据脱敏

**文件**: `src/xruntime/_runtime/_middleware/_redaction.py`

**职责**: 工具输入/输出中敏感数据正则脱敏，防止泄露到上下文/审计日志。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `RedactionRule` | 脱敏规则 (name/pattern/replacement) |
| `redact_text()` | 应用规则列表到文本 |
| `SecretRedactionMiddleware` | 中间件 (含默认规则集) |

**默认规则**:
- `api_key`: `sk-[a-zA-Z0-9]{20,}` → `[REDACTED_API_KEY]`
- `bearer_token`: `Bearer ...` → `Bearer [REDACTED_TOKEN]`
- `private_key`: `-----BEGIN ... PRIVATE KEY-----` → `[REDACTED_PRIVATE_KEY]`
- `password_assignment`: `password=...` → `password=[REDACTED]`

**测试**: 5 项

---

### 5. 多租户隔离

**文件**: `src/xruntime/_infra/_tenant.py`

**职责**: AS 的 `RedisStorage` key 只有 `user_id` 维度，XRuntime 通过
`tenant:{tid}:` 前缀实现完全隔离。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `TenantKeyPrefixer` | key 前缀注入器 (tenant:{tid}: + base_key) |
| `TenantContext` | 请求级租户上下文 (set/get/clear/context manager) |
| `TenantIsolationError` | 隔离违规异常 |

**隔离方式**: 所有 Redis key 被前缀化：
- `agentscope:user:alice:agent:c1` → `tenant:acme:agentscope:user:alice:agent:c1`
- 不同租户的相同 user_id 的 key 完全不同
- 空/None tenant_id 触发 `TenantIsolationError`

**测试**: 11 项

---

### 6. 迁移框架

**文件**: `src/xruntime/_runtime/_migrator.py`

**职责**: v1 仅骨架。旧 xruntime schema 不可用，不做自动数据转换。
检测旧版本会话并标记需手动迁移。

**核心类型**:

| 类型 | 说明 |
|---|---|
| `SCHEMA_VERSION` | 当前 schema 版本 (=1) |
| `SessionVersionChecker` | 版本检测器 (>=SCHEMA_VERSION 为 current) |
| `MigrationResult` | 迁移结果 (migrated/skipped/failed/errors) |
| `Migrator` | CLI 骨架 (dry_run/execute) |
| `MigrationShimMiddleware` | 加载时检测旧版本会话 |

**v1 策略**:
- current 版本会话 → skip
- 旧版本会话 → flag 为 failed + 错误信息 "manual migration required"
- 未来版本 → forward-compat (视为 current)

**测试**: 16 项

---

## 测试汇总

| 测试文件 | 测试数 | 覆盖模块 |
|---|---|---|
| `test_middlewares.py` | 34 | Audit + Quota + Rbac + Redaction |
| `test_tenant.py` | 11 | TenantKeyPrefixer + TenantContext |
| `test_migrator.py` | 16 | Migrator + MigrationShimMiddleware |
| **P2 合计** | **61** | **全部通过** |
| **累计 (P0+P1+P2)** | **171** | |

## 运行测试

```bash
cd agentscope
python3 -m pytest tests/xruntime/ -v -p no:cacheprovider
```

## 文件结构 (P2 新增)

```
src/xruntime/
├── _runtime/
│   ├── _middleware/
│   │   ├── _audit.py           # 审计日志中间件
│   │   ├── _quota.py           # 配额控制中间件
│   │   ├── _rbac.py            # RBAC 权限中间件
│   │   └── _redaction.py       # 敏感数据脱敏中间件
│   └── _migrator.py            # 迁移框架
├── _infra/
│   └── _tenant.py              # 多租户隔离

tests/xruntime/
├── test_middlewares.py         # 34 项测试
├── test_tenant.py              # 11 项测试
└── test_migrator.py            # 16 项测试
```

## 待完善 (后续阶段)

- **P3**: Orchestrator (DAG 编排) + SDK 包 + 插件体系
- **P4**: 部署 (Docker/Helm) + 可观测 (Metrics/Grafana) + 安全审计 + 压测
- **P5**: 迁移框架验收 + 全链路验收
- **后续**: SecretResolver (Vault/KMS) + 生产模式 event stream 对接 AS ChatService

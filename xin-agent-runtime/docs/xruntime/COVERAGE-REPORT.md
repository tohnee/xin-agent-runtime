# XRuntime 测试覆盖率报告

> 日期: 2026-06-25
> 测试总数: 422 passed
> 覆盖率工具: pytest-cov 7.1.0 + coverage 7.14.3

---

## 一、总览

| 指标 | 数值 |
|------|------|
| 项目整体覆盖率 | **85.4%** |
| 核心安全文件覆盖率 | **89.7%** |
| 测试总数 | 422 passed |
| 测试文件数 | 15 |

---

## 二、核心安全文件覆盖率

| 文件 | 语句数 | 未覆盖 | 覆盖率 | 安全逻辑 |
|------|--------|--------|--------|----------|
| `_gateway/_plan.py` | 58 | 0 | **100%** | RuntimeExecutionPlan 构建 + permissions 收紧 |
| `_runtime/_middleware/_quota.py` | 65 | 0 | **100%** | Token/cost 配额计量 + 阻断 |
| `_runtime/_workspace.py` | 24 | 0 | **100%** | 生产拒绝 local + path traversal guard |
| `_gateway/_request.py` | 24 | 0 | **100%** | 统一请求模型 |
| `_runtime/_tenant/__init__.py` | 3 | 0 | **100%** | RBAC 导出 |
| `_runtime/_middleware/_rbac.py` | 52 | 1 | **98.1%** | 角色规则匹配 |
| `_infra/_tenant.py` | 41 | 1 | **97.6%** | 租户 key prefix + contextvars |
| `_runtime/_tenant/_policy.py` | 69 | 3 | **95.7%** | Owner/Admin/Contributor/Viewer 权限矩阵 |
| `_runtime/_tenant/_store.py` | 70 | 3 | **95.7%** | ApiKeyStore + JwtParser + MembershipStore |
| `_runtime/_knowledge/_llm_wiki_adapter.py` | 274 | 16 | **94.2%** | BM25 + audit + redaction + scoped layout |
| `_runtime/_knowledge/_acl.py` | 47 | 3 | **93.6%** | KB ACL + per-KB 授权 |
| `_gateway/_ratelimit.py` | 51 | 5 | **90.2%** | 限流 middleware (429) |
| `_runtime/_model_governance.py` | 38 | 4 | **89.5%** | ModelRouter + tenant allowlist |
| `_runtime/_middleware/_audit.py` | 85 | 14 | **83.5%** | 审计 + JSON input 脱敏 |
| `_runtime/_langfuse.py` | 47 | 8 | **83.0%** | NoopExporter + payload 脱敏 |
| `_runtime/_middleware/_redaction.py` | 39 | 7 | **82.1%** | tool_call.input 脱敏 |
| `_gateway/_opencode_schema.py` | 35 | 8 | **77.1%** | OpenCode config 校验 |
| `_gateway/_auth.py` | 38 | 9 | **76.3%** | AuthMiddleware + ApiKeyStore 接入 |
| `_runtime/_knowledge/_tools.py` | 62 | 31 | **50.0%** | Search/Ingest 工具 check_permissions |
| **合计** | **1095** | **113** | **89.7%** | |

---

## 三、安全逻辑覆盖确认

### ✅ 完全覆盖 (100%) 的安全逻辑

| 模块 | 覆盖的安全逻辑 |
|------|---------------|
| `_plan.py` | RuntimeExecutionPlan 字段完整性、build_plan_from_request、permissions 只能收紧 |
| `_quota.py` | consume_tokens / consume_cost / consume_tool_call 超限阻断 |
| `_workspace.py` | 生产拒绝 local、docker 允许、path traversal guard、tenant/session 路径 |
| `_request.py` | XRuntimeRequest 字段、ProtocolType 枚举 |

### ✅ 高覆盖 (>90%) 的安全逻辑

| 模块 | 覆盖率 | 覆盖的安全逻辑 | 未覆盖 |
|------|--------|---------------|--------|
| `_rbac.py` | 98.1% | 角色规则匹配、allow/deny | 1 行边缘路径 |
| `_tenant.py` | 97.6% | key prefix 构建、contextvars 隔离 | 1 行初始化 |
| `_policy.py` | 95.7% | 四级角色权限矩阵、默认 deny | 3 行 from_member 边缘 |
| `_store.py` | 95.7% | API key 认证、JWT 解析、membership 解析 | 3 行边缘 |
| `_llm_wiki_adapter.py` | 94.2% | BM25、audit、redaction、scoped layout | 16 行 list/delete 边缘 |
| `_acl.py` | 93.6% | per-KB ACL、owner 检查、grant 检查 | 3 行边缘 |

### ⚠️ 需要关注的低覆盖模块

| 模块 | 覆盖率 | 问题 | 建议 |
|------|--------|------|------|
| `_tools.py` | 50.0% | check_permissions 的 ALLOW/DENY 返回路径未完全测试 | 补充 check_permissions 的 ALLOW/DENY 单元测试 |
| `_auth.py` | 76.3% | JWT Bearer 认证路径和 fail-closed 路径未完全测试 | 补充 AuthMiddleware dispatch 的端到端测试 |
| `_opencode_schema.py` | 77.1% | mcp/skills/plugins 校验分支未完全测试 | 补充各字段类型的校验测试 |

---

## 四、专项集成测试

### WorkspaceConfig + RBAC 集成测试

**文件**: `tests/xruntime/integration/test_workspace_rbac_integration.py`
**测试数**: 31 个

覆盖 5 个安全场景:

| 场景 | 测试数 | 覆盖内容 |
|------|--------|----------|
| Workspace 生产安全 | 8 | 生产拒绝 local、docker 允许、显式 override、path traversal、tenant/session 路径 |
| RBAC 权限矩阵 | 7 | Owner 全权限、Admin 不能删 tenant、Contributor 可 ingest、Viewer 只读、None deny、未知 action deny |
| Auth → RBAC 联动 | 6 | API Key 解析 principal、禁用 key 拒绝、membership 解析、disabled member 拒绝、AuthMiddleware 接入、anti-spoofing |
| Knowledge ACL | 7 | owner 访问、viewer 拒绝未授权 KB、grant 访问、跨租户不可见、authorized kb_ids、viewer 不能 ingest、contributor 可以 ingest |
| 多租户隔离 | 3 | 同用户不同租户不同角色、Redis key 隔离、API Key 绑定租户 |

---

## 五、覆盖率提升建议

| 优先级 | 模块 | 当前 | 目标 | 行动 |
|--------|------|------|------|------|
| P0 | `_tools.py` | 50% | 90% | 补充 check_permissions ALLOW/DENY 路径测试 |
| P1 | `_auth.py` | 76% | 90% | 补充 JWT dispatch + fail-closed 测试 |
| P1 | `_opencode_schema.py` | 77% | 90% | 补充 mcp/skills/plugins 校验测试 |
| P2 | `_redaction.py` | 82% | 90% | 补充 on_acting 脱敏路径测试 |
| P2 | `_audit.py` | 84% | 90% | 补充 streaming audit 路径测试 |
| P2 | `_langfuse.py` | 83% | 90% | 补充 enabled exporter trace 路径测试 |

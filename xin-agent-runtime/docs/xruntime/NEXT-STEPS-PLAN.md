# XRuntime 代码现状审查与下一步开发计划

> 日期: 2026-06-25
> 审查基准: ENTERPRISE-RUNTIME-ROADMAP.md (v0.1.0)
> 当前测试: 329 passed
> 当前分支: main @ 010bbcc

---

## 一、Milestone 完成度总览

| Milestone | 名称 | 状态 | 完成度 | 测试覆盖 |
|-----------|------|------|--------|----------|
| 0 | 测试护栏与文档 | ✅ 完成 | 100% | 329 tests |
| 1 | RBAC 与租户模型 | ✅ 完成 | 95% | 38 tests |
| 2 | Knowledge Scope 与 RBAC 贯穿 | ✅ 完成 | 90% | 38 tests |
| 3 | LLM-Wiki MVP | ⚠️ 部分 | 40% | 基础测试 |
| 4 | Protocol ExecutionPlan | ❌ 未开始 | 0% | 无 |
| 5 | Workspace 生产化 | ❌ 未开始 | 0% | 无 |
| 6 | Model Governance | ❌ 未开始 | 0% | 无 |
| 7 | Langfuse | ❌ 未开始 | 0% | 无 |

---

## 二、各 Milestone 详细审查

### Milestone 0: 测试护栏与文档 ✅

**已完成项:**
- ENTERPRISE-RUNTIME-ROADMAP.md 规划文档已保存
- 测试分类已建立 (tests/xruntime/)
- 协议 adapter contract 已固化 (test_anthropic_adapter, test_claude_code_adapter, test_opencode_adapter)
- Knowledge adapter contract 已固化 (test_knowledge.py)
- 21 项代码审查问题全部修复 (review-202606025.md)
- 修复报告已生成 (FIX-REPORT-20260625.md)

**缺失项:**
- ADR 文档目录未建立
- 测试未按 unit/contract/integration/e2e 分类（当前平铺在 tests/xruntime/）

### Milestone 1: RBAC 与租户模型 ✅ (95%)

**已完成项:**
- `TenantRole` 枚举: Owner/Admin/Contributor/Viewer ✅
- `Action` 枚举: 16 个细粒度 action ✅
- `TenantMember` / `Principal` 数据模型 ✅
- `TenantPolicy` 默认权限矩阵（符合 roadmap Action 矩阵）✅
- `PolicyDecision` 默认 deny ✅
- `TenantMembershipStore` / `ApiKeyStore` / `JwtClaimsParser` ✅
- `AuthMiddleware` 支持 API Key + JWT 双模式 ✅
- `AuthPrincipal` 从认证上下文绑定 tenant_id/user_id/role ✅
- Gateway middleware_factory 从 membership_store 解析 principal 并 assign_role ✅
- RBAC 默认角色从 "admin allow-all" 改为 least privilege (viewer) ✅
- RbacMiddleware 四级角色定义 (owner/admin/contributor/viewer) ✅

**已完成测试 (38 个):**
- test_rbac_policy.py: 权限矩阵验证
- test_rbac_defaults.py: 默认角色验证
- test_auth_membership.py: 认证主体绑定

**缺失项 (5%):**
- `AuthMiddleware` 在 `_server.py` 中仅检查 API key 集合，未接入 `ApiKeyStore` / `JwtClaimsParser`（构造了但未 wire 到请求路径）
- header tenant spoofing 拒绝测试仅有单元层，缺少端到端验证
- disabled member 无权限的端到端测试缺失

### Milestone 2: Knowledge Scope 与 RBAC 贯穿 ✅ (90%)

**已完成项:**
- `KnowledgeQuery` 增加 `tenant_id`、`user_id`、`kb_ids` 字段 ✅
- `KnowledgeChunk` metadata 携带 `tenant_id`、`kb_id` ✅
- `KnowledgeRegistry.retrieve()` 通过 `_chunk_in_scope()` 过滤越权 chunk ✅
- `KnowledgeAclStore` + `KnowledgeAclEntry` 实现 per-KB ACL ✅
- `KnowledgeMiddleware` 接收 `kb_ids` 参数，只检索授权 KB ✅
- `SearchKnowledgeTool` / `IngestKnowledgeTool` 接收 `tenant_id` ✅
- Gateway middleware_factory 从 `KnowledgeAclStore` 获取授权 kb_ids 并传入 middleware ✅
- `LlmWikiAdapter` ingest 时写入 tenant_id/kb_id metadata ✅

**已完成测试 (38 个):**
- test_knowledge_scope.py: 租户/KB 隔离检索
- test_knowledge_acl.py: per-KB ACL 权限过滤

**缺失项 (10%):**
- `SearchKnowledgeTool` / `IngestKnowledgeTool` 未在 `check_permissions` 中检查 KB ACL（当前只返回 PASSTHROUGH）
- Knowledge 工具的 `tenant_id` 来自构造时传入的默认值，未从请求上下文动态获取
- Agent auto-injection 不泄露未授权 KB 的端到端测试缺失

### Milestone 3: LLM-Wiki MVP ⚠️ (40%)

**已完成项:**
- `LlmWikiAdapter` 基础结构: raw/compiled 目录 ✅
- Markdown 分节 + keyword matching 检索 ✅
- `compile()` 生成 wiki page ✅
- tenant_id/kb_id metadata 写入 chunk ✅
- `_chunk_in_scope()` 过滤 ✅

**缺失项 (60%):**
- ❌ tenant/kb scoped path resolver（当前 raw_dir/compiled_dir 是全局的，未按 `tenants/{tenant_id}/kbs/{kb_id}/` 隔离）
- ❌ BM25 index（当前是简单 keyword matching）
- ❌ manifest.json index 文件
- ❌ markdown frontmatter 标准化
- ❌ 知识操作 audit log (knowledge-audit.jsonl)
- ❌ secret redaction before ingest
- ❌ source_id collision 跨租户隔离测试

### Milestone 4: Protocol ExecutionPlan ❌ (0%)

**完全未开始:**
- `RuntimeExecutionPlan` 类不存在
- 三个 adapter 仍直接输出 `XRuntimeRequest`，未经过 plan builder
- Claude Code metadata 字段（sandbox, max_budget_usd, model, fallback_model）未落地到执行语义
- OpenCode config 无 JSON Schema 校验
- permissions 只能收紧不能放宽的逻辑未实现

### Milestone 5: Workspace 生产化 ❌ (0%)

**完全未开始:**
- 无 `WorkspaceConfig` / `WorkspaceManagerFactory`
- 生产默认仍是 `LocalWorkspaceManager`
- 无 path traversal guard
- 无 tenant/session scoped workspace path

### Milestone 6: Model Governance ❌ (0%)

**完全未开始:**
- 无 `ModelCapabilityRegistry` / `ModelRouter`
- 无 tenant model allowlist
- 无 cost/token budget 强制（`QuotaMiddleware.on_model_call` 已实现计量，但无阻断策略）
- 无 provider health check

### Milestone 7: Langfuse ❌ (0%)

**完全未开始:**
- 无 `LangfuseConfig` / `LangfuseExporter`
- 无 Noop exporter
- 无 model/tool/knowledge/workflow span
- OTel tracing 已接入 (`_setup_otel`)，但 Langfuse 未接入

---

## 三、下一步开发计划

按 roadmap 第 10 节建议的顺序，结合当前完成度，下一步应按以下优先级推进：

### Phase 1: 补齐 M1-M2 遗漏项 (优先级: 高)

**目标:** 让 RBAC 和 Knowledge Scope 真正在请求路径上端到端生效。

| 任务 | 文件 | TDD 测试 |
|------|------|----------|
| AuthMiddleware 接入 ApiKeyStore/JwtClaimsParser | `_gateway/_auth.py`, `_server.py` | API key 解析到 principal；JWT claims 解析到 principal；header tenant spoofing 被拒绝 |
| Knowledge 工具 check_permissions 检查 KB ACL | `_knowledge/_tools.py` | Viewer 调用 ingest_knowledge 被拒；Viewer 只能搜索授权 KB |
| Knowledge 工具 tenant_id 从请求上下文动态获取 | `_knowledge/_tools.py`, `_middleware.py` | 不同租户的 agent 调用 search_knowledge 只返回本租户结果 |

### Phase 2: M3 LLM-Wiki MVP (优先级: 高)

**目标:** 让 `knowledge.backend: llm_wiki` 可作为生产知识库后端。

| 任务 | 文件 | TDD 测试 |
|------|------|----------|
| tenant/kb scoped path resolver | `_knowledge/_llm_wiki_adapter.py` | 不同 tenant/kb 的文件物理隔离；source_id collision 跨租户不冲突 |
| BM25 index 替换 keyword matching | `_knowledge/_llm_wiki_adapter.py` | BM25 能检索 exact keyword；score 排序正确 |
| manifest.json index 持久化 | `_knowledge/_llm_wiki_adapter.py` | compile 后 manifest 写入；重启后 index 可恢复 |
| 知识操作 audit log | `_knowledge/_llm_wiki_adapter.py` | ingest/compile/retrieve 操作写入 audit.jsonl |
| ingest 前 secret redaction | `_knowledge/_llm_wiki_adapter.py` | 含密钥的文档 ingest 前被脱敏 |

### Phase 3: M4 RuntimeExecutionPlan (优先级: 中)

**目标:** 三种协议统一经过 ExecutionPlan，Claude Code/OpenCode metadata 落地。

| 任务 | 文件 | TDD 测试 |
|------|------|----------|
| 新增 `RuntimeExecutionPlan` 模型 | `_gateway/_plan.py` (新建) | plan 字段完整性；从 XRuntimeRequest 构建 |
| 三个 adapter 输出 plan | `_gateway/_extension.py` | Anthropic/Claude Code/OpenCode 都产出 plan |
| Claude Code sandbox → workspace policy | `_gateway/_plan.py` | sandbox 字段映射到 workspace backend 选择 |
| Claude Code max_budget_usd → budget policy | `_gateway/_plan.py` | budget 超限时请求被拒 |
| OpenCode config JSON Schema 校验 | `_gateway/_opencode_adapter.py` | 无效 config 被拒；permissions 只能收紧 |
| permissions 只能收紧不能放宽 | `_gateway/_plan.py` | 客户端 allow 比 tenant policy 宽时被裁剪 |

### Phase 4: M5 Workspace 生产化 (优先级: 中)

| 任务 | 文件 | TDD 测试 |
|------|------|----------|
| `WorkspaceConfig` + `WorkspaceManagerFactory` | `_runtime/_workspace.py` (新建) | 默认 docker；生产 local 被拒绝 |
| tenant/session scoped path | `_runtime/_workspace.py` | path 包含 tenant/session |
| path traversal guard | `_runtime/_workspace.py` | `../` 被拒绝 |

### Phase 5: M6 Model Governance (优先级: 中)

| 任务 | 文件 | TDD 测试 |
|------|------|----------|
| `ModelCapabilityRegistry` | `_runtime/_model_governance.py` (新建) | 模型能力声明；tool-capable 任务选对模型 |
| `ModelRouter` + tenant allowlist | `_runtime/_model_governance.py` | 未允许模型被拒；fallback 生效 |
| cost/token budget 阻断 | `_runtime/_middleware/_quota.py` | cost 超限阻断（已有计量，加阻断） |

### Phase 6: M7 Langfuse (优先级: 低)

| 任务 | 文件 | TDD 测试 |
|------|------|----------|
| `LangfuseConfig` + Noop exporter | `_runtime/_langfuse.py` (新建) | disabled 时 no-op |
| Langfuse exporter | `_runtime/_langfuse.py` | model call 生成 generation；tool call 生成 span |
| secret redaction in payload | `_runtime/_langfuse.py` | payload 不含 secrets |

### Phase 7: 测试分类与 ADR (优先级: 低)

| 任务 | 说明 |
|------|------|
| 测试按 unit/contract/integration/e2e 重新分类 | 迁移到 tests/xruntime/{unit,contract,integration,e2e}/ |
| 建立 ADR 文档目录 | docs/adr/ 记录架构决策 |

---

## 四、推荐执行顺序

```
Phase 1 (补齐 M1-M2)  ──→  Phase 2 (M3 LLM-Wiki)  ──→  Phase 3 (M4 ExecutionPlan)
         │                                                    │
         └──→  Phase 4 (M5 Workspace)  ──→  Phase 5 (M6 Model)  ──→  Phase 6 (M7 Langfuse)
                                                                         │
                                                                    Phase 7 (测试分类)
```

**建议立即开始 Phase 1**，因为 M1-M2 的遗漏项（AuthMiddleware 未接入 ApiKeyStore、Knowledge 工具未检查 ACL）是安全漏洞——当前 RBAC 策略和 KB ACL 已定义但在请求路径上未完全生效。

每一步严格遵循 TDD:
```
write failing test → implement minimal code → pass test → add edge tests → docs → commit
```

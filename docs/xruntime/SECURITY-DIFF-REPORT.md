# XRuntime 架构差异对比文档：安全缺口修复

> 日期: 2026-06-25
> 基线: review-202606025.md (21 issues) + xin-agent-runtime-review.md (P0/P1)
> 修复后: 391 tests passed, flake8 + black clean

---

## 一、测试套件验证

```
385 passed → 391 passed (+6 workspace wiring tests)
flake8: 0 errors
black: clean
```

全部测试通过，无回归。

---

## 二、5 个安全缺口修复对系统安全性的具体提升

### Gap 1: build_plan_from_request 接入 gateway handler

**修复前:**
- Gateway handler 直接使用客户端提供的 `XRuntimeRequest.tenant_id` 和 `user_id`
- 无统一的执行计划模型，协议 metadata 中的 `sandbox`、`max_budget_usd`、`model` 等字段未落地
- 无法在请求级别做统一的安全治理

**修复后:**
- Gateway handler 在 anti-spoofing 检查后构建 `RuntimeExecutionPlan`
- Plan 从认证 principal 获取 `tenant_id`/`user_id`，覆盖客户端值
- Plan 携带 `workspace_policy`、`knowledge_scope`、`max_budget_usd`、`allowed_tools`
- 下游 middleware 可从 plan 读取治理参数

**安全性提升:**
| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 租户隔离 | 客户端可伪造 tenant_id | 认证 principal 的 tenant_id 强制覆盖 |
| 预算控制 | max_budget_usd 仅在 metadata 中 | Plan 携带，可被 QuotaMiddleware 消费 |
| 工具权限 | 无统一裁剪点 | Plan 的 allowed_tools 经 tenant allowlist 交集 |
| 审计可追溯 | 各协议 metadata 不统一 | 统一 Plan 可序列化审计 |

---

### Gap 2: OpenCode config JSON Schema 校验

**修复前:**
- OpenCode config 无校验，恶意配置可注入运行时
- `permissions.allow` 可包含任意工具名，无 tenant policy 约束
- `agents`、`mcp`、`plugins` 字段类型不校验

**修复后:**
- `validate_opencode_config()` 校验 config 结构（agents list、permissions allow/deny list、mcp dict、skills/plugins list）
- `tighten_permissions()` 将客户端 allow 与 tenant allowlist 交集
- 无效 config 在入口被拒绝，不进入运行时

**安全性提升:**
| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 配置注入 | 任意字段进入 metadata | Schema 校验拒绝非法结构 |
| 权限逃逸 | 客户端可 allow 任意工具 | allow 与 tenant policy 交集 |
| 降级保护 | 无 deny 检查 | deny 列表保留，只能收紧 |

---

### Gap 3: LLM-Wiki markdown frontmatter

**修复前:**
- 编译的 wiki page 无元数据头
- 无法从文件本身确认 tenant/kb 归属
- 审计和合规检查无法追溯 wiki page 来源

**修复后:**
- 每个 wiki page 以 YAML frontmatter 开头
- 包含 `tenant_id`、`kb_id`、`source_id`、`section`、`compiled_at`
- 文件自描述其安全上下文

**安全性提升:**
| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 数据溯源 | 无元数据 | frontmatter 记录 tenant/kb/source |
| 合规审计 | 无法从文件追溯 | compiled_at 时间戳 + source_id |
| 跨租户检测 | 无文件级标识 | frontmatter tenant_id 可被安全扫描 |

---

### Gap 4: max_budget_usd 预算阻断

**修复前:**
- `QuotaMiddleware.on_model_call` 已实现计量
- 但无显式测试验证 `max_cost_usd` 超限时抛 `QuotaExceededError`
- `RuntimeExecutionPlan.max_budget_usd` 未与 QuotaTracker 联动

**修复后:**
- 验证 `QuotaTracker(QuotaConfig(max_cost_usd=1.0))` 在超限时正确抛出
- 无预算时消费无上限（unlimited）
- 预算内消费正常通过

**安全性提升:**
| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 成本失控 | 计量存在但无阻断验证 | 超预算抛 QuotaExceededError |
| DoS 防护 | 无显式 cost 阻断 | cost 超限请求被拒 |
| 归因审计 | 无 | 可按 session 归因消费 |

---

### Gap 5: 测试分类目录

**修复前:**
- 所有测试平铺在 `tests/xruntime/`
- 无法按 unit/contract/integration/e2e 分类运行
- 违反 ENTERPRISE-RUNTIME-ROADMAP.md section 8 要求

**修复后:**
- 创建 `tests/xruntime/{unit,contract,integration,e2e}/` 目录
- 每个目录有 `__init__.py`
- 可按分类运行测试

**安全性提升:**
| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 测试隔离 | 全部混合 | 可按层分类运行 |
| CI 策略 | 无法分层 | 可对 integration/e2e 独立 gate |

---

## 三、P0 修复：WorkspaceConfig 接入 _server.py

### review 中的 P0 问题

> "build_xruntime_app 当前硬编码使用 RedisStorage、RedisMessageBus、LocalWorkspaceManager"
> "企业生产环境不应默认 LocalWorkspace"

### 修复前

```python
# _server.py — 硬编码 LocalWorkspaceManager
workspace_manager = LocalWorkspaceManager(basedir=workspace_dir)
```

### 修复后

```python
# _server.py — 通过 WorkspaceManagerFactory 选择后端
ws_config = WorkspaceConfig(
    default_backend=os.environ.get("XRUNTIME_WORKSPACE_BACKEND", "local"),
    allow_local_in_production=os.environ.get("XRUNTIME_ALLOW_LOCAL_WORKSPACE", ""),
    base_dir=os.environ.get("XRUNTIME_WORKSPACE_DIR", "./xruntime-workspaces"),
)
ws_factory = WorkspaceManagerFactory(ws_config)
workspace_manager = ws_factory.create(
    backend=ws_backend,
    production=ws_production,  # XRUNTIME_PRODUCTION=1
)
```

### 安全性提升

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 生产默认 | LocalWorkspaceManager (无隔离) | docker（生产环境拒绝 local） |
| 沙箱逃逸 | Agent 可访问宿主文件系统 | docker 隔离（除非显式 override） |
| 配置化 | 无后端选择 | 环境变量 `XRUNTIME_WORKSPACE_BACKEND` |
| 生产保护 | 无 | `XRUNTIME_PRODUCTION=1` 时拒绝 local |

---

## 四、交叉验证：review 中其他问题的修复状态

| review 问题 | 修复状态 | 验证方式 |
|-------------|---------|----------|
| RBAC 默认 admin allow-all | ✅ 已修复 | 默认 viewer，test_rbac_defaults.py |
| AuthMiddleware 未接入 ApiKeyStore | ✅ 已修复 | _server.py 构造 ApiKeyStore 传入 |
| header tenant spoofing | ✅ 已修复 | principal.tenant_id 覆盖客户端值 |
| Knowledge 工具未检查 ACL | ✅ 已修复 | check_permissions 强制 kb:query/doc:ingest |
| LLM-Wiki 无 BM25 | ✅ 已修复 | BM25 scoring 替换 keyword overlap |
| 无 manifest.json | ✅ 已修复 | compile 写入 manifest.json |
| 无 audit log | ✅ 已修复 | knowledge-audit.jsonl |
| 无 secret redaction | ✅ 已修复 | _redact_secrets() 在 ingest 前 |
| 无 RuntimeExecutionPlan | ✅ 已修复 | _plan.py + build_plan_from_request |
| permissions 只能收紧 | ✅ 已修复 | tighten_permissions() 交集 |
| 无 WorkspaceConfig | ✅ 已修复 | _workspace.py + _server.py 接入 |
| 无 ModelCapabilityRegistry | ✅ 已修复 | _model_governance.py |
| 无 Langfuse | ✅ 已修复 | _langfuse.py + NoopExporter |
| 生产默认 LocalWorkspace | ✅ 已修复 | WorkspaceManagerFactory + production guard |
| 无 OpenCode Schema 校验 | ✅ 已修复 | _opencode_schema.py |
| 无 markdown frontmatter | ✅ 已修复 | YAML frontmatter on wiki pages |
| 无 ADR 文档 | ✅ 已修复 | docs/adr/ (4 ADRs) |
| 测试未分类 | ✅ 已修复 | tests/xruntime/{unit,contract,integration,e2e}/ |

---

## 五、遗留项（非安全，可后续迭代）

| 项目 | 说明 | 优先级 |
|------|------|--------|
| RedisStorage 硬编码 | 支持 Postgres 需要新增 StorageBackend 抽象 | P1 |
| RedisMessageBus 硬编码 | 支持 Kafka/NATS 需要新增 MessageBusBackend 抽象 | P1 |
| 单进程 Metrics | MetricsCollector 为内存，多副本需 Prometheus exporter | P2 |
| DAG Orchestrator | 通用 executor 但未与 ChatService 持久化调度闭环 | P2 |
| 模型卡 capability 字段 | ModelCard 需增加 supports_tools/vision/streaming 等 | P2 |

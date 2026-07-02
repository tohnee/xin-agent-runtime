# Plan: Xin Agent Runtime — 下一阶段全量开发

**Source**: 用户需求 + 技术债清单
**Complexity**: Large (9 个子项目，跨 4 个优先级)

## Summary
在 606 个测试通过的基础上，完成真实 LLM 端到端验证、Sandbox 接入、记忆系统升级 (LLM 提取 + Redis 持久化 + 向量检索 V2)、ToolBase 适配、以及 P2 级别的 OTel/UI/多模型路由/技能市场。全部使用 TDD 开发。

## Patterns to Mirror

| Category | Source | Pattern |
|---|---|---|
| 模型调用 | `src/agentscope/model/_openai_chat.py` | OpenAIChatModel(config_name, model_name, api_key, base_url) |
| 模型调用 | `src/agentscope/model/_anthropic.py` | AnthropicChatModel 兼容 Anthropic 协议 |
| Sandbox | `src/agentscope/app/workspace_manager/_docker_workspace_manager.py` | DockerWorkspaceManager(basedir, base_image, ...) async context manager |
| 工具注册 | `src/agentscope/tool/_adapters.py:31` | FunctionTool 继承 ToolBase, 需实现 check_permissions |
| 中间件 | `src/xruntime/_runtime/_middleware/_base.py` | MiddlewareBase, is_implemented() 检测 hook |
| 测试 | `tests/xruntime/test_*.py` | pytest + pytest-asyncio, @pytest.fixture, tmp_path |
| 日志 | `src/xruntime/_runtime/_middleware/_llm_error_handling.py` | logging.getLogger("xruntime.middleware.xxx") |
| 配置 | `src/xruntime/_config.py` | Pydantic BaseModel, ObservabilityConfig 模式 |

## Implementation Phases

---

### Phase 1 (P0): 真实 LLM 端到端测试

**目标**: 接入 Ark API (兼容 OpenAI/Anthropic 协议)，构造真实 Agent 执行场景

**API 信息**:
- Anthropic 兼容: `https://ark.cn-beijing.volces.com/api/plan`
- OpenAI 兼容: `https://ark.cn-beijing.volces.com/api/plan/v3`
- API Key: `ark-1300f8d7-0482-41df-bc77-c8a58eaa1240-89be3`
- 模型: glm-5.2, minimax-m3, kimi-k2.7-code

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_llm_test_config.py` | CREATE | Ark API 模型配置 (OpenAI/Anthropic 两种协议) |
| `tests/xruntime/e2e/test_real_llm_e2e.py` | CREATE | 真实 LLM 端到端测试 (需 API Key) |
| `tests/xruntime/e2e/conftest.py` | CREATE | E2E fixtures (模型实例, Agent 创建) |
| `pytest.ini` | UPDATE | 添加 e2e marker, 默认跳过 (需 --run-e2e) |

**Tasks**:

#### Task 1.1: 创建 Ark API 模型配置
- **Action**: 创建 `_llm_test_config.py`, 封装 OpenAIChatModel + AnthropicChatModel 配置
- **Mirror**: `src/agentscope/model/_openai_chat.py` 的构造参数
- **Validate**: `python3 -c "from xruntime._runtime._llm_test_config import create_ark_models; m = create_ark_models(); print(m)"`

#### Task 1.2: 真实 LLM 单轮对话测试 (TDD)
- **Action**: 测试 Agent 调用 glm-5.2 完成简单问答
- **Validate**: `pytest tests/xruntime/e2e/test_real_llm_e2e.py -v --run-e2e`

#### Task 1.3: 真实 LLM + 工具调用测试 (TDD)
- **Action**: Agent 调用模型 + bash 工具, 验证 LoopDetection + LLMErrorHandling 中间件
- **Validate**: `pytest tests/xruntime/e2e/test_real_llm_e2e.py::test_real_llm_with_tools --run-e2e`

#### Task 1.4: 真实 LLM + 完整中间件链测试 (TDD)
- **Action**: 验证 9 中间件链 + 技能注入 + 记忆注入 + Langfuse trace
- **Validate**: `pytest tests/xruntime/e2e/test_real_llm_e2e.py::test_real_llm_full_chain --run-e2e`

#### Task 1.5: 真实 LLM + 子 Agent 委派测试 (TDD)
- **Action**: 主 Agent 委派任务给子 Agent, 验证 TaskTool + SubAgentExecutor
- **Validate**: `pytest tests/xruntime/e2e/test_real_llm_e2e.py::test_real_llm_subagent --run-e2e`

**Risks**:
| Risk | Likelihood | Mitigation |
|---|---|---|
| API Key 泄露 | Medium | 使用环境变量, 不硬编码 |
| API 不可用 | Medium | 测试默认跳过, 需显式 --run-e2e |
| 模型响应慢 | Low | 设置 timeout_seconds=30 |

---

### Phase 2 (P0): Sandbox 接入

**目标**: 替换 `_workspace.py` Placeholder 为 AgentScope DockerWorkspaceManager

**现有代码**: AgentScope 已有完整实现:
- `DockerWorkspaceManager` — Docker 容器沙箱 (自定义镜像/MCP/TTL)
- `E2BWorkspaceManager` — E2B 云沙箱
- `LocalWorkspaceManager` — 本地工作区

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_workspace.py` | UPDATE | 替换 Placeholder 为 DockerWorkspaceManager 接入 |
| `src/xruntime/_config.py` | UPDATE | 添加 WorkspaceConfig (backend/basedir/base_image) |
| `src/xruntime/_gateway/_extension.py` | UPDATE | 根据配置选择 workspace manager |
| `tests/xruntime/test_workspace_integration.py` | CREATE | Sandbox 接入测试 |

**Tasks**:

#### Task 2.1: 创建 WorkspaceConfig (TDD)
- **Action**: 在 XRuntimeConfig 中添加 workspace 配置 (backend=docker/local, basedir, base_image)
- **Validate**: `python3 -c "from xruntime._config import XRuntimeConfig; c=XRuntimeConfig(); print(c.workspace)"`

#### Task 2.2: 替换 Workspace Placeholder (TDD)
- **Action**: `_workspace.py` 根据配置返回 DockerWorkspaceManager 或 LocalWorkspaceManager
- **Validate**: `pytest tests/xruntime/test_workspace_integration.py -v`

#### Task 2.3: Docker 沙箱集成测试 (TDD)
- **Action**: 测试 Agent 在 Docker 容器中执行 bash 命令
- **Validate**: `pytest tests/xruntime/test_workspace_integration.py::test_docker_workspace --run-docker`

**Risks**:
| Risk | Likelihood | Mitigation |
|---|---|---|
| Docker 未安装 | Medium | 测试默认跳过, 需 --run-docker |
| 镜像构建慢 | High | 使用默认镜像, 缓存层 |
| 权限问题 | Low | basedir 使用 /tmp |

---

### Phase 3 (P1): MemoryMiddleware LLM 提取

**目标**: 用 LLM 替代启发式提取, 提升记忆质量

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_memory/_extractor.py` | CREATE | LLMMemoryExtractor (调用模型提取记忆) |
| `src/xruntime/_runtime/_memory/_middleware.py` | UPDATE | 注入 LLMMemoryExtractor 替代启发式 |
| `tests/xruntime/test_memory_extractor.py` | CREATE | LLM 提取测试 (mock + real) |

**Tasks**:

#### Task 3.1: 创建 LLMMemoryExtractor (TDD)
- **Action**: 调用 LLM 从对话中提取 preference/fact/procedure/episode
- **Pattern**: 输入 events → LLM prompt → JSON 解析 → MemoryItem[]
- **Validate**: `pytest tests/xruntime/test_memory_extractor.py -v`

#### Task 3.2: 集成到 MemoryMiddleware (TDD)
- **Action**: `_extract_memories` 改为调用 LLMMemoryExtractor
- **Validate**: `pytest tests/xruntime/test_memory_extractor.py::test_middleware_with_llm_extraction -v`

---

### Phase 4 (P1): 向量检索 V2

**目标**: 接入 sentence-transformers 替代 trigram 哈希

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_memory/_embedding_providers.py` | CREATE | SentenceTransformersProvider + OpenAIEmbeddingProvider |
| `src/xruntime/_runtime/_memory/_hybrid_retriever.py` | UPDATE | 支持新 provider |
| `tests/xruntime/test_embedding_providers.py` | CREATE | 向量检索 V2 测试 |

**Tasks**:

#### Task 4.1: 创建 SentenceTransformersProvider (TDD)
- **Action**: 实现 EmbeddingProvider protocol, 使用 all-MiniLM-L6-v2
- **Validate**: `pytest tests/xruntime/test_embedding_providers.py -v`

#### Task 4.2: 混合检索对比测试 (TDD)
- **Action**: 同一查询, 对比 trigram vs sentence-transformers 的检索质量
- **Validate**: `pytest tests/xruntime/test_embedding_providers.py::test_retrieval_quality_comparison -v`

---

### Phase 5 (P1): Redis 持久化

**目标**: MemoryStore 从内存版升级到 Redis 后端

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_memory/_redis_store.py` | CREATE | RedisMemoryStore (实现与 MemoryStore 相同接口) |
| `src/xruntime/_runtime/_memory/_store.py` | UPDATE | 添加 backend 参数, 支持 memory/redis 切换 |
| `tests/xruntime/test_redis_store.py` | CREATE | Redis 持久化测试 |

**Tasks**:

#### Task 5.1: 创建 RedisMemoryStore (TDD)
- **Action**: 实现 add/get/search/delete/clear, 使用 Redis hash + sorted set
- **Validate**: `pytest tests/xruntime/test_redis_store.py -v --run-redis`

#### Task 5.2: 后端切换测试 (TDD)
- **Action**: MemoryStore 根据配置自动选择 memory 或 redis 后端
- **Validate**: `pytest tests/xruntime/test_redis_store.py::test_backend_switch -v`

---

### Phase 6 (P2): ToolBase 适配

**目标**: LoadSkillTool/TaskTool 继承 ToolBase, 实现自动注册

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_skills/_load_skill_tool.py` | UPDATE | 继承 ToolBase, 实现 check_permissions |
| `src/xruntime/_runtime/_subagents/_task_tool.py` | UPDATE | 继承 ToolBase, 实现 check_permissions |
| `tests/xruntime/test_tool_base_adapter.py` | CREATE | ToolBase 适配测试 |

---

### Phase 7 (P2): OTel tracing (可选)

**目标**: 标准化 OpenTelemetry 分布式追踪

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_middleware/_otel_tracer.py` | CREATE | OTelTracerMiddleware |
| `src/xruntime/_infra/_otel.py` | CREATE | OTel exporter 配置 |
| `tests/xruntime/test_otel_tracer.py` | CREATE | OTel 测试 |

---

### Phase 8 (P2): 多模型路由

**目标**: 按任务复杂度自动路由到不同模型

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_model_router.py` | CREATE | ModelRouter (simple/complex → model) |
| `src/xruntime/_runtime/_middleware/_model_router_middleware.py` | CREATE | 路由中间件 |
| `tests/xruntime/test_model_router.py` | CREATE | 路由测试 |

---

### Phase 9 (P2): 前端管理 UI

**目标**: Web 界面查看 sessions/metrics/memories/skills

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_web/app.py` | CREATE | FastAPI 管理界面 |
| `src/xruntime/_web/templates/` | CREATE | Jinja2 模板 |
| `tests/xruntime/test_web_ui.py` | CREATE | UI 测试 |

---

### Phase 10 (P2): 技能市场

**目标**: 支持从外部仓库安装技能

**Files to Change**:

| File | Action | Why |
|---|---|---|
| `src/xruntime/_runtime/_skills/_marketplace.py` | CREATE | SkillMarketplace (git clone + install) |
| `tests/xruntime/test_skill_marketplace.py` | CREATE | 市场测试 |

---

## Validation

```bash
# 单元测试 (默认运行, 不需要外部依赖)
pytest tests/xruntime -q

# E2E 测试 (需要 API Key)
pytest tests/xruntime/e2e/ -v --run-e2e

# Docker 测试 (需要 Docker)
pytest tests/xruntime/test_workspace_integration.py --run-docker

# Redis 测试 (需要 Redis)
pytest tests/xruntime/test_redis_store.py --run-redis

# Lint
black --line-length=79 --check src/xruntime tests/xruntime
flake8 --extend-ignore=E203,W503,E704 src/xruntime

# CI 全量
./scripts/run_ci.sh
```

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Ark API 不稳定 | Medium | High | 超时重试 + mock fallback |
| Docker 环境差异 | Medium | Medium | 测试默认跳过 |
| sentence-transformers 安装重 | High | Low | 延迟导入 + 可选依赖 |
| Redis 连接失败 | Low | Medium | fallback 到内存版 |
| ToolBase API 变化 | Low | Medium | 锁定 AS 版本 |

## Acceptance

- [ ] Phase 1: 真实 LLM E2E 测试通过 (4 个场景)
- [ ] Phase 2: Docker 沙箱接入, Agent 可在容器中执行代码
- [ ] Phase 3: LLM 记忆提取替代启发式
- [ ] Phase 4: sentence-transformers 向量检索
- [ ] Phase 5: Redis 持久化
- [ ] Phase 6: ToolBase 适配
- [ ] Phase 7-10: P2 项按需推进
- [ ] 全部使用 TDD: 先写测试 → 实现 → 验收
- [ ] 所有测试通过: `pytest tests/xruntime -q`
- [ ] Lint 通过: `black + flake8`

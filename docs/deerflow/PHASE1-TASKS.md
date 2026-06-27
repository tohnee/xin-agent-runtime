# 阶段一开发任务清单：循环检测 + LLM 错误处理

> 日期: 2026-06-26
> 预计工作量: 1 天
> 状态: 待开发

---

## 一、AgentScope 沙箱确认

**结论**: AgentScope 已提供完整沙箱实现，阶段三沙箱升级只需正确接入。

| 沙箱类 | 路径 | 构造参数 |
|--------|------|----------|
| `DockerWorkspaceManager` | `agentscope.app.workspace_manager._docker_workspace_manager` | `basedir, base_image, node_version, extra_pip, gateway_port, env, ttl, sweep_interval` |
| `E2BWorkspaceManager` | `agentscope.app.workspace_manager._e2b_workspace_manager` | `basedir, api_key, ...` |
| `LocalWorkspaceManager` | `agentscope.app.workspace_manager._local_workspace_manager` | `basedir` |

所有 Manager 继承 `WorkspaceManagerBase(ABC)`，实现 `get_workspace()` / `create_workspace()` / `close()` / `close_all()`。DockerWorkspaceManager 支持自定义镜像、MCP 注入、skill_paths、TTL 淘汰、异步上下文管理器。

---

## 二、代码文件结构

```
src/xruntime/_runtime/_middleware/
├── __init__.py                      # 已有
├── _audit.py                        # 已有
├── _quota.py                        # 已有
├── _rbac.py                         # 已有
├── _redaction.py                    # 已有
├── _loop_detection.py               # 新增 — 循环检测中间件
└── _llm_error_handling.py           # 新增 — LLM 错误处理中间件

tests/xruntime/
├── test_loop_detection.py           # 新增 — 循环检测测试
└── test_llm_error_handling.py       # 新增 — LLM 错误处理测试
```

---

## 三、任务 0a：循环检测中间件 (LoopDetectionMiddleware)

### 3.1 目标

检测 Agent 重复执行相同工具调用（相同工具名 + 相同参数），在超过阈值时阻断并注入提示让 Agent 换策略。

### 3.2 设计

```python
# src/xruntime/_runtime/_middleware/_loop_detection.py

class LoopDetectionConfig(BaseModel):
    """循环检测配置"""
    max_repeats: int = 3              # 相同调用最大重复次数
    window_size: int = 10             # 检测窗口（最近 N 次工具调用）
    block_message: str = (
        "You seem to be repeating the same action. "
        "Try a different approach or ask the user for clarification."
    )

class LoopDetectionMiddleware(MiddlewareBase):
    """检测重复工具调用，防止 Agent 死循环"""

    def __init__(self, config: LoopDetectionConfig | None = None):
        self._config = config or LoopDetectionConfig()
        self._history: list[tuple[str, str]] = []  # [(tool_name, args_hash), ...]

    async def on_acting(self, agent, context, *args, **kwargs):
        """在工具执行前检查是否重复"""
        tool_name = context.current_tool_name
        args_hash = self._hash_args(context.tool_input)

        # 记录本次调用
        self._history.append((tool_name, args_hash))

        # 只检查窗口内
        window = self._history[-self._config.window_size:]

        # 统计相同调用次数
        current = (tool_name, args_hash)
        repeat_count = sum(1 for item in window if item == current)

        if repeat_count > self._config.max_repeats:
            # 阻断 + 注入提示
            return BlockResult(
                message=self._config.block_message,
            )

    def _hash_args(self, args: Any) -> str:
        """对工具参数生成 hash"""
        import hashlib, json
        return hashlib.md5(
            json.dumps(args, sort_keys=True, default=str).encode()
        ).hexdigest()

    def reset(self):
        """重置历史（新会话时调用）"""
        self._history.clear()
```

### 3.3 检测逻辑

```
Agent 调用 Bash(command="ls")
  → history: [(Bash, hash1)]
  → repeat_count=1 → OK

Agent 调用 Bash(command="ls")  (第 2 次)
  → history: [(Bash, hash1), (Bash, hash1)]
  → repeat_count=2 → OK

Agent 调用 Bash(command="ls")  (第 3 次)
  → history: [..., (Bash, hash1)]
  → repeat_count=3 → OK (max_repeats=3)

Agent 调用 Bash(command="ls")  (第 4 次)
  → history: [..., (Bash, hash1)]
  → repeat_count=4 > max_repeats=3 → BLOCK!
  → 注入: "You seem to be repeating..."
```

### 3.4 测试用例

| 测试 | 说明 |
|------|------|
| `test_no_repeat_allowed` | 不同工具调用不触发 |
| `test_same_tool_different_args` | 相同工具不同参数不触发 |
| `test_repeat_within_limit` | 重复次数 ≤ max_repeats 不触发 |
| `test_repeat_exceeds_limit_blocked` | 重复次数 > max_repeats 被阻断 |
| `test_window_size_resets` | 超出窗口的旧记录不计数 |
| `test_reset_clears_history` | reset() 清空历史 |
| `test_block_message_injected` | 阻断时注入正确消息 |

---

## 四、任务 0b：LLM 错误处理中间件 (LLMErrorHandlingMiddleware)

### 4.1 目标

捕获 LLM 调用异常，按策略重试/降级/熔断，防止 Agent 因瞬时错误崩溃。

### 4.2 设计

```python
# src/xruntime/_runtime/_middleware/_llm_error_handling.py

class LLMErrorHandlingConfig(BaseModel):
    """LLM 错误处理配置"""
    max_retries: int = 3              # 最大重试次数
    retry_delay: float = 1.0          # 初始重试延迟（秒）
    retry_backoff: float = 2.0        # 退避倍数
    max_delay: float = 30.0           # 最大延迟
    fallback_model: str = ""          # 降级模型（空=不降级）
    circuit_breaker_threshold: int = 5  # 连续失败熔断阈值
    circuit_breaker_reset_time: float = 60.0  # 熔断恢复时间（秒）

class CircuitState(Enum):
    CLOSED = "closed"      # 正常
    OPEN = "open"           # 熔断中
    HALF_OPEN = "half_open" # 半开（试探）

class LLMErrorHandlingMiddleware(MiddlewareBase):
    """LLM 错误处理 — 重试 + 降级 + 熔断"""

    def __init__(self, config: LLMErrorHandlingConfig | None = None):
        self._config = config or LLMErrorHandlingConfig()
        self._consecutive_failures = 0
        self._circuit_state = CircuitState.CLOSED
        self._circuit_opened_at: float = 0

    async def on_model_call(self, agent, context, *args, **kwargs):
        """模型调用前检查熔断状态"""
        if self._circuit_state == CircuitState.OPEN:
            if time.time() - self._circuit_opened_at > self._config.circuit_breaker_reset_time:
                self._circuit_state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpenError(
                    f"Circuit breaker is OPEN. "
                    f"Retry after {self._config.circuit_breaker_reset_time}s."
                )

    async def on_model_call_error(
        self, agent, context, error, *args, **kwargs
    ):
        """模型调用失败后处理"""
        self._consecutive_failures += 1

        # 熔断检查
        if self._consecutive_failures >= self._config.circuit_breaker_threshold:
            self._circuit_state = CircuitState.OPEN
            self._circuit_opened_at = time.time()
            return ErrorResult(
                message=f"Circuit breaker opened after "
                        f"{self._consecutive_failures} failures.",
                should_retry=False,
            )

        # 降级检查
        if self._config.fallback_model:
            context.switch_model(self._config.fallback_model)
            return ErrorResult(
                message=f"Falling back to {self._config.fallback_model}",
                should_retry=True,
            )

        # 重试
        if context.retry_count < self._config.max_retries:
            delay = min(
                self._config.retry_delay * (
                    self._config.retry_backoff ** context.retry_count
                ),
                self._config.max_delay,
            )
            await asyncio.sleep(delay)
            return ErrorResult(
                message=f"Retrying (attempt {context.retry_count + 1})",
                should_retry=True,
            )

        return ErrorResult(
            message=f"Max retries ({self._config.max_retries}) exceeded.",
            should_retry=False,
        )

    async def on_model_call_success(self, agent, context, *args, **kwargs):
        """模型调用成功后重置计数器"""
        self._consecutive_failures = 0
        if self._circuit_state == CircuitState.HALF_OPEN:
            self._circuit_state = CircuitState.CLOSED
```

### 4.3 错误处理流程

```
LLM 调用
  ↓
[on_model_call] 检查熔断状态
  ├── CLOSED → 继续
  ├── HALF_OPEN → 继续试探
  └── OPEN → 拒绝 (CircuitBreakerOpenError)
  ↓
LLM 执行
  ├── 成功 → [on_model_call_success] 重置计数器
  └── 失败 → [on_model_call_error]
      ↓
    连续失败 ≥ 5? → 熔断 OPEN
      ↓
    有降级模型? → 切换模型 + 重试
      ↓
    重试次数 < 3? → 指数退避重试
      ↓
    都不行 → 返回错误
```

### 4.4 测试用例

| 测试 | 说明 |
|------|------|
| `test_success_resets_failures` | 成功调用重置失败计数 |
| `test_retry_on_error` | 错误时按指数退避重试 |
| `test_max_retries_exceeded` | 超过最大重试次数返回错误 |
| `test_fallback_model_switch` | 有降级模型时切换 |
| `test_circuit_breaker_opens` | 连续失败 ≥ 阈值触发熔断 |
| `test_circuit_breaker_blocks` | 熔断 OPEN 时拒绝调用 |
| `test_circuit_breaker_half_open` | 超时后进入 HALF_OPEN |
| `test_circuit_breaker_closes` | HALF_OPEN 成功后关闭熔断 |
| `test_retry_delay_backoff` | 退避延迟正确计算 |

---

## 五、开发顺序

| 步骤 | 内容 | 预计 |
|------|------|------|
| 1 | 创建 `_loop_detection.py` | 1h |
| 2 | 创建 `test_loop_detection.py` + 运行 | 1h |
| 3 | 创建 `_llm_error_handling.py` | 1.5h |
| 4 | 创建 `test_llm_error_handling.py` + 运行 | 1.5h |
| 5 | 接入 `create_xruntime_extension` 中间件链 | 0.5h |
| 6 | black + flake8 + 全量测试验证 | 0.5h |
| **合计** | | **~6h (0.75 天)** |

---

## 六、集成点

### 6.1 中间件链接入

在 `src/xruntime/_gateway/_mw_state.py` 的中间件工厂中添加：

```python
def create_middlewares(user_id, agent_id, session_id):
    return [
        AuditMiddleware(...),
        QuotaMiddleware(...),
        RbacMiddleware(...),
        SecretRedactionMiddleware(...),
        LoopDetectionMiddleware(config=loop_config),     # 新增
        LLMErrorHandlingMiddleware(config=error_config),  # 新增
    ]
```

### 6.2 配置接入

在 `src/xruntime/_config.py` 的 `XRuntimeConfig` 中添加：

```python
class XRuntimeConfig(BaseModel):
    ...
    loop_detection:
        LoopDetectionConfig = LoopDetectionConfig()
    llm_error_handling:
        LLMErrorHandlingConfig = LLMErrorHandlingConfig()
```

YAML 配置:
```yaml
loop_detection:
  max_repeats: 3
  window_size: 10

llm_error_handling:
  max_retries: 3
  retry_delay: 1.0
  retry_backoff: 2.0
  fallback_model: ""
  circuit_breaker_threshold: 5
  circuit_breaker_reset_time: 60.0
```

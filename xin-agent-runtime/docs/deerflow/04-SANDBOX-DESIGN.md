# 沙箱环境设计文档：XAR vs DeerFlow 对比与升级方案

> 日期: 2026-06-26
> 状态: 设计草案
> 模块: Sandbox / Workspace

---

## 一、现状对比

### 1.1 Xin Agent Runtime 现状

| 能力 | 状态 | 实现 |
|------|------|------|
| 后端选择 (local/docker/e2b) | ✅ 配置层 | `WorkspaceConfig` + `WorkspaceManagerFactory` |
| 生产拒绝 local | ✅ | `allow_local_in_production=False` |
| Path traversal guard | ✅ | `workspace_path()` 检查 `..` 和 `/` |
| Docker 沙箱 | ❌ Placeholder | `create("docker")` 返回空壳对象 |
| E2B 沙箱 | ❌ Placeholder | 同上 |
| 虚拟文件系统 | ❌ | 无 |
| 网络隔离 | ❌ | 无 |
| 资源限制 (CPU/Memory/Time) | ❌ | 无 |
| 沙箱审计 | ❌ | 无 |
| 沙箱生命周期管理 | ❌ | 无 |
| 多租户沙箱隔离 | ✅ 路径级 | `tenants/{tid}/sessions/{sid}/` |

**核心问题**: `WorkspaceManagerFactory.create()` 对 docker/e2b 只返回 Placeholder，没有实际的沙箱执行能力。

### 1.2 DeerFlow 现状

| 能力 | 状态 | 实现 |
|------|------|------|
| Local 沙箱 | ✅ | `LocalSandboxProvider` — 直接在宿主执行 |
| Docker 沙箱 | ✅ | `AioSandboxProvider` — Docker 容器隔离 |
| K8s 沙箱 | ✅ | Kubernetes pod 隔离 |
| 虚拟文件系统 | ✅ | 虚拟路径映射 (`/mnt/user-data/` → 实际路径) |
| 网络控制 | ❌ | 未实现网络白名单 |
| 资源限制 | ❌ | 依赖 Docker 默认 |
| 沙箱审计 | ✅ | `SandboxAuditMiddleware` — bash 命令安全审计 |
| 命令执行 | ✅ | `execute_command()` |
| 文件操作 | ✅ | `read_file/write_file/list_dir/glob/grep/update_file` |
| 懒加载/预加载 | ✅ | sandbox 可在首次使用时创建 |

---

## 二、DeerFlow 值得借鉴的设计

### 2.1 虚拟文件系统

DeerFlow 使用虚拟路径映射，Agent 看到的路径始终是 `/mnt/user-data/` 前缀，实际映射到宿主或容器内的路径。这简化了 Agent prompt 中的路径引用，也防止路径泄露。

```python
# DeerFlow 模式
agent_sees = "/mnt/user-data/uploads/report.csv"
actual_path = "/tmp/sandbox-abc123/uploads/report.csv"
```

### 2.2 SandboxProvider 抽象

DeerFlow 将沙箱抽象为 Provider 接口，Agent 不关心底层是 Local/Docker/K8s：

```python
class SandboxProvider:
    async def execute_command(self, cmd: str) -> CommandResult: ...
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def list_dir(self, path: str) -> list[str]: ...
    async def glob(self, pattern: str) -> list[str]: ...
    async def grep(self, pattern: str, path: str) -> list[str]: ...
    async def update_file(self, path: str, old: str, new: str) -> None: ...
```

### 2.3 沙箱审计中间件

DeerFlow 的 `SandboxAuditMiddleware` 在每次 bash 执行前检查命令安全性：
- 危险命令检测（`rm -rf /`, `curl | sh` 等）
- 环境变量泄露检测
- 网络访问审计
- 命令日志记录

---

## 三、XAR 升级设计方案

### 3.1 目标

将 Placeholder 替换为可执行的沙箱实现，同时保持 XAR 的多租户隔离和安全优势。

### 3.2 选型建议

| 方案 | 复杂度 | 推荐度 | 说明 |
|------|--------|--------|------|
| A: 直接用 AgentScope 已有的 DockerWorkspace | 低 | ⭐⭐⭐ | AgentScope 内核已有 DockerWorkspace，只需正确接入 |
| B: 自建 SandboxProvider 抽象 | 高 | ⭐ | 重复造轮子，不建议 |
| C: 参考 DeerFlow AioSandboxProvider | 中 | ⭐⭐ | 可借鉴虚拟路径和审计 |

**推荐方案 A**：AgentScope 已有 `DockerWorkspace` 和 `E2BWorkspace` 实现，XAR 的 `WorkspaceManagerFactory` 只需正确创建这些实例，而不是返回 Placeholder。

### 3.3 核心改动

#### 3.3.1 修复 WorkspaceManagerFactory

```python
class WorkspaceManagerFactory:
    def create(self, backend: str | None = None, production: bool = False) -> Any:
        effective = backend or self._config.default_backend

        # 安全检查（已有）
        if production and effective == "local" and not self._config.allow_local_in_production:
            raise ValueError(...)

        if effective == "local":
            from agentscope.app.workspace_manager import LocalWorkspaceManager
            return LocalWorkspaceManager(basedir=self._config.base_dir)

        if effective == "docker":
            from agentscope.app.workspace_manager import DockerWorkspaceManager
            return DockerWorkspaceManager(
                basedir=self._config.base_dir,
                image=self._config.docker_image,      # 新增
                network=self._config.docker_network,   # 新增: "none" = 隔离
                memory_limit=self._config.docker_memory,  # 新增
                cpu_limit=self._config.docker_cpu,     # 新增
            )

        if effective == "e2b":
            from agentscope.app.workspace_manager import E2BWorkspaceManager
            return E2BWorkspaceManager(
                api_key=os.environ.get("E2B_API_KEY", ""),
            )

        raise ValueError(f"Unknown backend: {effective}")
```

#### 3.3.2 扩展 WorkspaceConfig

```python
class WorkspaceConfig(BaseModel):
    default_backend: str = "docker"
    allow_local_in_production: bool = False
    base_dir: str = "./xruntime-workspaces"

    # 新增: Docker 配置
    docker_image: str = "python:3.11-slim"
    docker_network: str = "none"          # "none" = 无网络隔离
    docker_memory: str = "512m"           # 内存限制
    docker_cpu: str = "1.0"               # CPU 限制
    docker_timeout: int = 300             # 执行超时 (秒)

    # 新增: 沙箱审计
    audit_enabled: bool = True
    audit_log_path: str = ""              # 空 = 日志到 stdout

    # 新增: 虚拟路径映射
    virtual_path_prefix: str = "/workspace"  # Agent 看到的路径前缀
```

#### 3.3.3 SandboxAuditMiddleware

```python
class SandboxAuditMiddleware(MiddlewareBase):
    """沙箱命令审计 — 在 tool_call 前检查安全性"""

    DANGEROUS_PATTERNS = [
        r"rm\s+-rf\s+/",
        r"curl\s+.*\|\s*sh",
        r"wget\s+.*\|\s*bash",
        r"chmod\s+777",
        r":\(\)\s*\{.*\};",  # fork bomb
    ]

    async def on_acting(self, context):
        tool_name = context.current_tool_name
        if tool_name in ("bash", "execute_command"):
            cmd = context.tool_input.get("command", "")
            # 1. 危险命令检测
            for pattern in self.DANGEROUS_PATTERNS:
                if re.search(pattern, cmd):
                    return BlockResult(
                        reason=f"Dangerous command blocked: {pattern}"
                    )
            # 2. 环境变量泄露检测
            if re.search(r"(API_KEY|SECRET|PASSWORD|TOKEN)\s*=", cmd, re.I):
                return BlockResult(
                    reason="Environment variable assignment blocked"
                )
            # 3. 审计日志
            self._audit_log(context, cmd)
```

#### 3.3.4 虚拟路径映射

```python
class VirtualPathMapper:
    """Agent 看到的虚拟路径 ↔ 实际沙箱路径"""

    def __init__(self, prefix: str = "/workspace", actual_root: str = ""):
        self._prefix = prefix
        self._root = actual_root

    def to_virtual(self, actual_path: str) -> str:
        """实际路径 → 虚拟路径"""
        rel = os.path.relpath(actual_path, self._root)
        return os.path.join(self._prefix, rel)

    def to_actual(self, virtual_path: str) -> str:
        """虚拟路径 → 实际路径"""
        if virtual_path.startswith(self._prefix):
            rel = virtual_path[len(self._prefix):].lstrip("/")
            actual = os.path.join(self._root, rel)
            # Path traversal guard
            if not os.path.realpath(actual).startswith(
                os.path.realpath(self._root)
            ):
                raise ValueError("Path traversal detected")
            return actual
        return virtual_path
```

### 3.4 配置

```yaml
workspace:
  default_backend: docker
  allow_local_in_production: false
  docker_image: "python:3.11-slim"
  docker_network: "none"        # 生产隔离
  docker_memory: "512m"
  docker_cpu: "1.0"
  docker_timeout: 300
  audit_enabled: true
  virtual_path_prefix: "/workspace"
```

---

## 四、复杂度评估

| 组件 | 工作量 | 说明 |
|------|--------|------|
| 修复 Factory (接入真实 Docker/E2B) | 1 天 | 替换 Placeholder |
| 扩展 WorkspaceConfig | 0.5 天 | 新增 Docker 配置字段 |
| SandboxAuditMiddleware | 1 天 | 危险命令检测 + 审计日志 |
| VirtualPathMapper | 0.5 天 | 虚拟路径映射 |
| 网络隔离 (docker network=none) | 0.5 天 | Docker 网络配置 |
| 资源限制 | 0.5 天 | Docker memory/cpu 限制 |
| 测试 | 1.5 天 | Factory/Audit/PathMapper/集成 |
| **合计** | **~5-6 天** | |

---

## 五、与 DeerFlow 的差异保留

XAR 不需要照搬 DeerFlow 的所有沙箱功能，保留以下独有优势：

| XAR 优势 | DeerFlow 无 |
|----------|------------|
| 多租户沙箱隔离 (`tenants/{tid}/sessions/{sid}/`) | ✅ |
| 生产拒绝 local guard | ❌ |
| Path traversal guard (tenant_id/session_id 级) | ❌ |
| 与 RBAC 联动 (viewer 不能 bash) | ❌ |

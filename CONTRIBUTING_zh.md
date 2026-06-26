# 贡献指南

欢迎为 Xin Agent Runtime 贡献代码！

## 开发路线图

开发计划发布在 [GitHub Issues](https://github.com/tohnee/xin-agent-runtime/issues) 和 [GitHub Projects](https://github.com/tohnee/xin-agent-runtime/projects)。

## 快速开始

1. 在 GitHub 上 fork [tohnee/xin-agent-runtime](https://github.com/tohnee/xin-agent-runtime)。
2. 克隆你的 fork：

   ```bash
   git clone https://github.com/<your-username>/xin-agent-runtime.git
   cd xin-agent-runtime
   git remote add upstream https://github.com/tohnee/xin-agent-runtime.git
   ```

3. 安装开发依赖：

   ```bash
   uv pip install -e ".[dev]"
   pre-commit install
   ```

## 代码规范

- **Python 3.11+**
- **black** `--line-length=79`
- **flake8** `--extend-ignore=E203`
- **pre-commit** 格式检查
- 新代码必须有测试（TDD 优先）
- 覆盖率门槛：80%

## Pull Request 流程

1. 推送分支到你的 fork。
2. 对 `tohnee/xin-agent-runtime:main` 发起 PR。
3. 确保 CI 通过（lint + tests + security gate）。
4. 等待 review，approved 后 squash-merge。

## 测试

```bash
# 全部 XRuntime 测试
pytest tests/xruntime -q

# 带覆盖率
pytest tests/xruntime --cov=xruntime --cov-report=term-missing

# 仅集成测试
pytest tests/xruntime/integration -v
```

## 社区

- [Discussions](https://github.com/tohnee/xin-agent-runtime/discussions)
- [Issues](https://github.com/tohnee/xin-agent-runtime/issues)

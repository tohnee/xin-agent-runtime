# Contributing to Xin Agent Runtime

We welcome contributions from the community! This document covers the
development workflow, coding standards, and PR process.

## Development Roadmap

The development roadmap is tracked via [GitHub Issues](https://github.com/tohnee/xin-agent-runtime/issues)
and [GitHub Projects](https://github.com/tohnee/xin-agent-runtime/projects).

## Getting Started

1. Fork [tohnee/xin-agent-runtime](https://github.com/tohnee/xin-agent-runtime) on GitHub.
2. Clone your fork:

   ```bash
   git clone https://github.com/<your-username>/xin-agent-runtime.git
   cd xin-agent-runtime
   git remote add upstream https://github.com/tohnee/xin-agent-runtime.git
   ```

3. Install in development mode:

   ```bash
   uv pip install -e ".[dev]"
   pre-commit install
   ```

4. Create a feature branch:

   ```bash
   git checkout -b feat/your-feature
   ```

## Coding Standards

- **Python 3.11+** required
- **black** with `--line-length=79`
- **flake8** with `--extend-ignore=E203`
- **pre-commit** hooks for formatting and linting
- All new code must have tests (TDD preferred)
- Test coverage threshold: 80%

## Pull Request Process

1. Push your branch to your fork.
2. Open a PR against `tohnee/xin-agent-runtime:main`.
3. Ensure CI passes (lint + tests + security gate).
4. Request review from maintainers.
5. Squash-merge after approval.

## Testing

```bash
# Run all XRuntime tests
pytest tests/xruntime -q

# Run with coverage
pytest tests/xruntime --cov=xruntime --cov-report=term-missing

# Run integration tests only
pytest tests/xruntime/integration -v
```

## Community

- [Discussions](https://github.com/tohnee/xin-agent-runtime/discussions)
- [Issues](https://github.com/tohnee/xin-agent-runtime/issues)

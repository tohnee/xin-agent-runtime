# 阶段二：SkillRegistry + MemorySystem 开发任务清单

> 日期: 2026-06-26
> 预计工作量: 5-6 天
> 状态: 开发中

---

## 一、SkillRegistry 开发任务

### 1.1 代码文件结构

```
src/xruntime/_runtime/_skills/
├── __init__.py                    # 包导出
├── _manifest.py                   # SkillManifest + SkillContent 模型
├── _registry.py                   # SkillRegistry (扫描/解析/缓存)
└── _load_skill_tool.py            # LoadSkillTool (Agent 按需加载技能)

skills/                            # 技能定义目录
├── public/                        # 内置技能
│   ├── research/
│   │   └── SKILL.yaml
│   ├── coding/
│   │   └── SKILL.yaml
│   └── data-analysis/
│       └── SKILL.yaml
└── custom/                        # 用户自定义 (空目录 + .gitkeep)

tests/xruntime/
├── test_skill_registry.py         # Registry 测试
└── test_load_skill_tool.py        # Tool 测试
```

### 1.2 SKILL.yaml 格式

```yaml
name: research
description: >
  Conduct multi-source web research with citations.
  Use when user asks for research, analysis, or fact-checking.
version: "1.0.0"
allowed_tools: [web_search, browse, read_file, write_file]
permissions: [network, filesystem:read]
instructions: |
  # Research Skill
  
  ## When to Use
  - User requests research or analysis
  - Need factual information with citations
  
  ## Workflow
  1. Break down research question into sub-queries
  2. Search multiple sources for each sub-query
  3. Cross-reference findings
  4. Generate cited summary
```

### 1.3 任务分解

| # | 任务 | 文件 | 预计 |
|---|------|------|------|
| 1 | SkillManifest + SkillContent 模型 | `_manifest.py` | 0.5h |
| 2 | SkillRegistry (扫描/YAML解析/缓存) | `_registry.py` | 1.5h |
| 3 | LoadSkillTool | `_load_skill_tool.py` | 0.5h |
| 4 | __init__.py 导出 | `__init__.py` | 0.1h |
| 5 | 内置技能 (3个 SKILL.yaml) | `skills/public/` | 1h |
| 6 | test_skill_registry.py (8测试) | tests/ | 1h |
| 7 | test_load_skill_tool.py (3测试) | tests/ | 0.5h |
| 8 | lint + 集成验证 | - | 0.5h |
| **合计** | | | **~5.5h** |

### 1.4 测试用例

| 测试 | 说明 |
|------|------|
| `test_discover_finds_skills` | 扫描目录发现所有 SKILL.yaml |
| `test_discover_empty_dir` | 空目录返回空列表 |
| `test_get_manifest_existing` | 获取已注册技能的 manifest |
| `test_get_manifest_nonexistent` | 不存在的技能返回 None |
| `test_load_content_full` | 加载完整技能内容含 instructions |
| `test_load_content_nonexistent` | 加载不存在技能抛异常 |
| `test_inject_to_system_prompt` | 格式化技能列表为 system prompt |
| `test_allowed_tools_parsed` | allowed_tools 正确解析为 list |

---

## 二、MemorySystem MVP 开发任务

### 2.1 代码文件结构

```
src/xruntime/_runtime/_memory/
├── __init__.py                    # 包导出
├── _models.py                     # MemoryItem 模型
├── _store.py                      # MemoryStore (Redis CRUD + 关键词检索)
└── _middleware.py                 # MemoryMiddleware (注入 + 提取)

tests/xruntime/
├── test_memory_store.py           # Store 测试
└── test_memory_middleware.py      # Middleware 测试
```

### 2.2 任务分解

| # | 任务 | 文件 | 预计 |
|---|------|------|------|
| 1 | MemoryItem 模型 | `_models.py` | 0.5h |
| 2 | MemoryStore (CRUD + 关键词检索) | `_store.py` | 1.5h |
| 3 | MemoryMiddleware (注入 + 后台提取) | `_middleware.py` | 1.5h |
| 4 | __init__.py 导出 | `__init__.py` | 0.1h |
| 5 | test_memory_store.py (8测试) | tests/ | 1h |
| 6 | test_memory_middleware.py (5测试) | tests/ | 1h |
| 7 | lint + 集成验证 | - | 0.5h |
| **合计** | | | **~6h** |

### 2.3 测试用例

| 测试 | 说明 |
|------|------|
| `test_add_and_get_memory` | 添加记忆并按 ID 获取 |
| `test_search_keyword_match` | 关键词匹配检索 |
| `test_search_no_match` | 无匹配返回空列表 |
| `test_search_top_k` | top_k 限制返回数量 |
| `test_delete_memory` | 删除记忆 |
| `test_tenant_isolation` | 不同租户记忆隔离 |
| `test_expired_memory_filtered` | 过期记忆被过滤 |
| `test_confidence_filter` | 低置信度记忆被过滤 |

| 测试 | 说明 |
|------|------|
| `test_inject_memories_to_prompt` | 检索记忆注入 system prompt |
| `test_no_memories_no_injection` | 无记忆时不注入 |
| `test_extract_memories_async` | 后台提取不阻塞 |
| `test_memory_format_correct` | 注入格式正确 |
| `test_reset_per_session` | 会话隔离 |

---

## 三、实施顺序

| 步骤 | 内容 | 预计 |
|------|------|------|
| 1 | SkillRegistry 全部代码 + 测试 | 5.5h |
| 2 | MemorySystem 全部代码 + 测试 | 6h |
| 3 | lint + 全量测试 (473 + ~24) | 0.5h |
| **合计** | | **~12h (1.5 天)** |

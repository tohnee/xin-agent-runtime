# P3-C 详细测试用例清单

> Credential Broker 硬化:RedisCredentialStore / AutoRotation / ScopeHierarchy
> 开发方法:TDD(Red → Green → Refactor)
> 测试框架:pytest + pytest-asyncio + fakeredis

---

## 一、测试用例总览

| 模块 | 测试文件 | 测试类 | 测试数 |
|------|----------|--------|--------|
| ScopeHierarchy | `tests/xruntime/test_scope_hierarchy.py` | TestScopeHierarchy | 12 |
| RedisCredentialStore | `tests/xruntime/test_redis_credential_store.py` | TestRedisCredentialStore | 18 |
| AutoRotation | `tests/xruntime/test_credential_auto_rotation.py` | TestAutoRotation | 15 |
| Config 扩展 | `tests/xruntime/test_credential_config_p3c.py` | TestCredentialConfigP3C | 8 |
| 集成测试 | `tests/xruntime/integration/test_p3c_integration.py` | TestP3CIntegration | 10 |
| **合计** | | | **63** |

---

## 二、Task 1: ScopeHierarchy 层级解析(12 测试)

### 2.1 TestScopeHierarchyExpand(层级展开)— 6 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 1 | `test_expand_admin_includes_all_children` | hierarchy={admin: [chat, embed, tool_use]} | expand("admin") → {admin, chat, embed, tool_use} |
| 2 | `test_expand_tool_use_includes_chat` | hierarchy={tool_use: [chat]} | expand("tool_use") → {tool_use, chat} |
| 3 | `test_expand_leaf_scope_returns_self` | hierarchy={tool_use: [chat]} | expand("chat") → {chat} |
| 4 | `test_expand_unknown_scope_returns_self` | hierarchy={admin: [chat]} | expand("unknown") → {unknown} |
| 5 | `test_expand_multiple_scopes_union` | hierarchy={admin: [chat], tool_use: [embed]} | expand(["admin", "tool_use"]) → {admin, chat, tool_use, embed} |
| 6 | `test_expand_empty_hierarchy_returns_input` | hierarchy={} | expand(["chat", "embed"]) → {chat, embed} |

### 2.2 TestScopeHierarchyValidate(层级校验)— 4 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 7 | `test_validate_admin_satisfies_chat` | cred.scopes=["admin"], required=["chat"], hierarchy={admin: [chat]} | is_valid=True |
| 8 | `test_validate_tool_use_satisfies_chat` | cred.scopes=["tool_use"], required=["chat"], hierarchy={tool_use: [chat]} | is_valid=True |
| 9 | `test_validate_chat_does_not_satisfy_admin` | cred.scopes=["chat"], required=["admin"], hierarchy={admin: [chat]} | is_valid=False, missing=["admin"] |
| 10 | `test_validate_no_hierarchy_falls_back_to_exact` | cred.scopes=["chat"], required=["chat"], hierarchy={} | is_valid=True |

### 2.3 TestScopeHierarchyCycleDetection(循环检测)— 2 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 11 | `test_cycle_detection_raises` | hierarchy={a: [b], b: [a]} | raises ValueError("cycle") |
| 12 | `test_self_cycle_detection_raises` | hierarchy={a: [a]} | raises ValueError("cycle") |

---

## 三、Task 2: RedisCredentialStore(18 测试)

### 3.1 TestRedisCredentialStoreBasicCRUD(基本 CRUD)— 5 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 1 | `test_save_and_load_round_trip` | 保存凭证 → 加载 | 加载的凭证 == 保存的凭证(所有字段) |
| 2 | `test_load_unknown_returns_none` | 加载不存在的 credential_id | 返回 None |
| 3 | `test_delete_removes_credential` | 保存 → 删除 → 加载 | 第二次加载返回 None |
| 4 | `test_delete_unknown_returns_false` | 删除不存在的 id | 返回 False |
| 5 | `test_save_overwrites_existing` | 同一 credential_id 保存两次 | 第二次加载返回最新版本 |

### 3.2 TestRedisCredentialStoreTTL(TTL 过期)— 4 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 6 | `test_ttl_set_on_save` | 保存 TTL=3600 的凭证 | Redis EXPIRE 被设置,ttl > 0 |
| 7 | `test_expired_credential_returns_none_on_load` | 保存 → 模拟过期 → 加载 | 返回 None |
| 8 | `test_load_skips_expired` | 保存过期凭证 → 加载 | 返回 None(不返回过期数据) |
| 9 | `test_ttl_none_means_persistent` | 保存 TTL=None 的凭证 | Redis EXPIRE 不设置(-1) |

### 3.3 TestRedisCredentialStoreMultiTenant(多租户隔离)— 4 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 10 | `test_tenant_a_cannot_load_tenant_b_credential` | tenant A 保存 → tenant B 加载 | 返回 None(隔离) |
| 11 | `test_key_prefix_includes_tenant_id` | tenant_id="t1" | Redis key 含 "tenant:t1:" 前缀 |
| 12 | `test_list_by_tenant_returns_only_own` | tenant A 保存 2 个,tenant B 保存 1 个 | list_by_tenant("A") 返回 2 个 |
| 13 | `test_delete_by_tenant_only_removes_own` | tenant A 保存 2 个,tenant B 保存 1 个 → delete_by_tenant("A") | 返回 2,tenant B 的凭证仍在 |

### 3.4 TestRedisCredentialStoreSessionIndex(会话索引)— 3 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 14 | `test_index_by_session_tuple` | 保存 (t,s,r) → 按 session 查找 | 返回正确的 credential_id |
| 15 | `test_session_index_overwrites_on_reissue` | 同一 (t,s,r) 保存两次 | 索引指向最新 credential_id |
| 16 | `test_session_index_cleanup_on_delete` | 保存 → 删除 → 按 session 查找 | 返回 None |

### 3.5 TestRedisCredentialStoreFallback(降级)— 2 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 17 | `test_redis_unavailable_raises_connection_error` | 连接不存在的 Redis | raises redis.ConnectionError |
| 18 | `test_redis_reconnect_after_failure` | 第一次失败 → 重连 → 成功 | 第二次操作成功 |

---

## 四、Task 3: AutoRotation 自动轮换(15 测试)

### 4.1 TestAutoRotationTrigger(轮换触发)— 4 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 1 | `test_rotation_triggered_when_within_buffer` | 凭证 TTL=3600,距过期 <300s(buffer) | auto_rotate() 返回新凭证,旧凭证标记 rotating |
| 2 | `test_rotation_not_triggered_when_outside_buffer` | 凭证 TTL=3600,距过期 >300s | auto_rotate() 返回 None(无需轮换) |
| 3 | `test_rotation_not_triggered_for_expired` | 凭证已过期 | auto_rotate() 返回 None(已过期,不轮换) |
| 4 | `test_rotation_not_triggered_for_revoked` | 凭证已撤销 | auto_rotate() 返回 None |

### 4.2 TestAutoRotationGracePeriod(宽限期)— 3 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 5 | `test_old_credential_stays_valid_during_grace` | 轮换后 grace period=60s | 旧凭证在 60s 内仍可 validate |
| 6 | `test_old_credential_revoked_after_grace` | 轮换后 grace period 过期 | 旧凭证 validate 返回 invalid |
| 7 | `test_drain_invalidations_includes_rotated` | 轮换 → grace 过期 → drain | 返回旧 credential_id |

### 4.3 TestAutoRotationSessionContinuity(会话连续性)— 3 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 8 | `test_rotated_credential_replaces_session_index` | 轮换后 issue_for_session | 返回新凭证(非旧凭证) |
| 9 | `test_rotation_preserves_scopes_and_audience` | 旧凭证 scopes=["chat"], audience="sb-1" | 新凭证 scopes/audience 相同 |
| 10 | `test_rotation_updates_ttl` | 旧凭证 TTL=3600,新凭证 TTL=3600 | 新凭证 expires_at > 旧凭证 expires_at |

### 4.4 TestAutoRotationBatch(批量轮换)— 3 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 11 | `test_rotate_all_returns_count` | 5 个凭证,3 个在 buffer 内 | rotate_all() 返回 3 |
| 12 | `test_rotate_all_skips_revoked` | 5 个凭证,1 个已撤销 | 撤销的不被轮换 |
| 13 | `test_rotate_all_empty_cache_returns_zero` | 无凭证 | rotate_all() 返回 0 |

### 4.5 TestAutoRotationDisabled(禁用场景)— 2 测试

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 14 | `test_auto_rotate_disabled_returns_none` | config.auto_rotate=False | auto_rotate() 返回 None |
| 15 | `test_rotate_all_disabled_returns_zero` | config.auto_rotate=False | rotate_all() 返回 0 |

---

## 五、Task 4: Config 扩展(8 测试)

### 5. TestCredentialConfigP3C

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 1 | `test_default_config_has_no_redis` | 默认 CredentialBrokerConfig | store_backend="memory", redis_url="" |
| 2 | `test_redis_config_from_yaml` | YAML 配置 redis_url + key_prefix | 正确解析 |
| 3 | `test_redis_config_from_env` | XRUNTIME_CREDENTIAL_BROKER_REDIS_URL env | 正确覆盖 |
| 4 | `test_auto_rotate_default_false` | 默认配置 | auto_rotate=False |
| 5 | `test_auto_rotate_enabled` | auto_rotate=True + rotation_buffer_seconds=300 | 正确解析 |
| 6 | `test_scope_hierarchy_default_empty` | 默认配置 | scope_hierarchy={} |
| 7 | `test_scope_hierarchy_from_yaml` | YAML 配置层级 | 正确解析 |
| 8 | `test_allowed_scopes_with_hierarchy` | allowed_scopes + scope_hierarchy 共存 | 两者独立校验 |

---

## 六、集成测试(10 测试)

### TestP3CIntegration

| # | 测试名 | 场景 | 预期 |
|---|--------|------|------|
| 1 | `test_redis_store_with_broker_issue_validate` | broker + RedisStore:issue → validate | 凭证从 Redis 加载,validate 通过 |
| 2 | `test_redis_store_survives_broker_restart` | broker A issue → broker B(同 Redis) validate | broker B 能加载凭证 |
| 3 | `test_auto_rotation_with_redis_store` | RedisStore + auto_rotate | 轮换后新凭证在 Redis 中,旧凭证标记 rotating |
| 4 | `test_scope_hierarchy_in_validate` | broker + hierarchy:issue "admin" → validate required "chat" | 通过(admin 包含 chat) |
| 5 | `test_scope_hierarchy_in_issue` | broker + hierarchy:issue "tool_use" → 凭证 scopes=["tool_use", "chat"] | 自动展开 |
| 6 | `test_redis_multi_tenant_isolation` | tenant A issue → tenant B validate | tenant B 无法加载 tenant A 的凭证 |
| 7 | `test_auto_rotation_drain_invalidations` | 轮换 → grace 过期 → drain | 旧 credential_id 在 drain 集合中 |
| 8 | `test_redis_store_fallback_to_memory` | Redis 不可用时降级到 InMemory | 不崩溃,功能降级 |
| 9 | `test_full_lifecycle_redis_rotation_hierarchy` | issue(scope 层级) → validate → auto_rotate → drain | 全链路通过 |
| 10 | `test_no_secret_leak_in_redis` | 保存到 Redis 的数据 | 不含 api_key 明文 |

---

## 七、边界场景重点验证

### 7.1 Redis 自动旋转边界

| 边界 | 验证点 |
|------|--------|
| TTL=0 的凭证 | 不触发轮换(已过期) |
| buffer > TTL | 签发后立即触发轮换(不陷入死循环) |
| 并发轮换同一凭证 | 只产生一个新凭证(锁机制) |
| 轮换时 Redis 不可用 | 旧凭证不撤销(grace 延长) |
| grace period 内凭证被手动 revoke | 不重复撤销 |

### 7.2 权限层级边界

| 边界 | 验证点 |
|------|--------|
| 空 scopes + hierarchy | expand([]) → {} |
| hierarchy 中有未知 scope | expand 时保留,不报错 |
| 多层嵌套(a→b→c) | expand("a") → {a, b, c} |
| 菱形依赖(a→b, a→c, b→d, c→d) | expand("a") → {a, b, c, d}(d 不重复) |
| hierarchy 与 allowed_scopes 冲突 | allowed_scopes 优先,拒绝不在 allowlist 的 scope |

### 7.3 Redis 多租户边界

| 边界 | 验证点 |
|------|--------|
| tenant_id 为空字符串 | key 前缀为 "tenant::" (不崩溃) |
| tenant_id 含特殊字符(如 ":") | key 正确拼接,不产生 key 注入 |
| 同一 Redis 不同 key_prefix | 两个 broker 实例互不干扰 |
| Redis FLUSHDB 后 | 所有凭证丢失,broker 降级 |

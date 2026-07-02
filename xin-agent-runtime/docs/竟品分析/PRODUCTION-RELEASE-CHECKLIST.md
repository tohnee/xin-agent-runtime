# XRuntime 生产环境上线检查清单

> 版本：v4.0
> 日期：2026-07-01
> 基础：1371 tests passed, 9 新模块 100% 覆盖率
> 用法：逐项检查，全部 ✅ 后方可上线

---

## 一、代码与测试

### 1.1 测试验证

- [ ] **全量测试通过**: `pytest tests/xruntime -q` → 0 failed
- [ ] **新增模块覆盖率 100%**: P4-A/B/C/D 所有新模块
- [ ] **无 flaky 测试**: 连续运行 3 次，结果一致
- [ ] **基准无回退**: `python scripts/benchmark.py --all --compare baseline.json`
- [ ] **告警模拟测试通过**: `pytest tests/xruntime/test_alert_simulation.py`

### 1.2 代码质量

- [ ] **black 格式化**: `black --line-length=79 --check src/ tests/`
- [ ] **flake8 无错误**: `flake8 --max-line-length=79 --extend-ignore=E203,W503,E501 src/`
- [ ] **无未提交改动**: `git status` → clean
- [ ] **无敏感信息**: `grep -r "api_key\|secret\|password" src/ --include="*.py"` (排除注释)

### 1.3 依赖检查

- [ ] **Python 版本**: 3.11+
- [ ] **opentelemetry-sdk**: >=1.39.0
- [ ] **redis**: >=5.0
- [ ] **pydantic**: >=2.0
- [ ] **无 CVE 漏洞**: `pip audit`

---

## 二、基础设施

### 2.1 Redis

- [ ] **Redis 版本**: 7.0+
- [ ] **连接 URL 可达**: `redis-cli ping` → PONG
- [ ] **内存配置**: `maxmemory` >= 512MB
- [ ] **淘汰策略**: `maxmemory-policy allkeys-lru`
- [ ] **持久化**: AOF 或 RDB 已开启
- [ ] **密码保护**: `requirepass` 已设置(生产)
- [ ] **多租户前缀**: `tenant:{tid}:creds:` 已验证

### 2.2 网络

- [ ] **端口 8900**: XRuntime API 可达
- [ ] **端口 6379**: Redis 可达
- [ ] **端口 4317**: OTel Collector gRPC 可达
- [ ] **端口 9090**: Prometheus 可达
- [ ] **端口 9093**: Alertmanager 可达
- [ ] **端口 9200**: Elasticsearch 可达
- [ ] **TLS 证书**: 生产环境 HTTPS 已配置

### 2.3 磁盘

- [ ] **审计日志目录**: `/var/log/xruntime/` 可写
- [ ] **磁盘空间**: >= 50GB 可用
- [ ] **日志轮转**: logrotate 或 ILM 已配置

---

## 三、可观测性

### 3.1 OTel 追踪

- [ ] **OTel Collector 运行**: `curl http://otel-collector:8889/metrics`
- [ ] **OTLP 端点**: `XRUNTIME_OBSERVABILITY_OTEL_ENDPOINT` 已设置
- [ ] **Root span 生成**: 运行 test workflow → Jaeger 有 `workflow.run` span
- [ ] **Child span 生成**: Jaeger 有 `workflow.step.<id>` span
- [ ] **采样率**: 生产 10% (`sampling_percentage: 10`)

### 3.2 Prometheus 指标

- [ ] **指标端点**: `curl http://localhost:8900/metrics` 返回数据
- [ ] **指标包含**:
  - [ ] `workflow_step_total`
  - [ ] `workflow_step_duration_ms_avg`
  - [ ] `workflow_checkpoint_save_total`
  - [ ] `workflow_resume_total`
  - [ ] `xruntime_credential_expiry_seconds`
  - [ ] `xruntime_auto_rotation_failures_total`
  - [ ] `xruntime_approval_timed_out_total`
  - [ ] `xruntime_approval_pending_count`
  - [ ] `xruntime_redis_pool_active_connections`
  - [ ] `xruntime_redis_pool_max_connections`
- [ ] **Prometheus 抓取**: `http://prometheus:9090/targets` 全部 UP

### 3.3 告警规则

- [ ] **规则已加载**: `curl http://prometheus:9090/api/v1/rules` → 9 条
- [ ] **告警 1**: WorkflowHighFailureRate (critical)
- [ ] **告警 2**: WorkflowStepHighLatency (warning)
- [ ] **告警 3**: CheckpointSaveFailure (critical)
- [ ] **告警 4**: CredentialExpiringSoon (warning)
- [ ] **告警 5**: AutoRotationFailure (critical)
- [ ] **告警 6**: ApprovalTimeout (warning)
- [ ] **告警 7**: ApprovalBacklog (warning)
- [ ] **告警 8**: OtelCollectorHighMemory (warning)
- [ ] **告警 9**: RedisConnectionPoolExhausted (critical)

### 3.4 Alertmanager

- [ ] **Alertmanager 运行**: `curl http://alertmanager:9093/-/healthy` → OK
- [ ] **Slack webhook**: 已配置且可达
- [ ] **PagerDuty webhook**: 已配置且可达(critical 告警)
- [ ] **通知路由**: critical → PagerDuty, warning → Slack

### 3.5 审计日志

- [ ] **审计日志写入**: 运行 workflow → `/var/log/xruntime/audit.jsonl` 有内容
- [ ] **Filebeat 运行**: `docker logs filebeat` 无错误
- [ ] **ES 索引创建**: `curl http://es:9200/_cat/indices/xruntime-audit*`
- [ ] **Kibana 可查**: Kibana → Discover → 有审计日志
- [ ] **合规字段**: 审计条目包含 timestamp/tenant_id/workflow_id/step_id/agent/status
- [ ] **无密钥泄漏**: 审计条目不含 api_key/secret

---

## 四、凭证安全

- [ ] **Redis 凭证存储**: api_key base64 编码(不明文)
- [ ] **多租户隔离**: tenant A 无法加载 tenant B 凭证
- [ ] **TTL 配置**: 凭证有 expires_at
- [ ] **自动轮换**: `XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED=true`
- [ ] **轮换阈值**: `auto_rotate_threshold` 已设置
- [ ] **Scope 层级**: ScopeHierarchy 无循环
- [ ] **Redis 密码**: 生产环境 `requirepass` 已设置

---

## 五、并发与性能

### 5.1 ConcurrencyPool

- [ ] **全局并行度**: `max_concurrent_steps` 已设置(建议 100/Pod)
- [ ] **per-agent 限制**: `per_agent_concurrency` 已设置(建议 10)
- [ ] **超时配置**: `acquire_timeout` = 30s
- [ ] **利用率监控**: 告警在 > 90% 时触发

### 5.2 RateLimiter

- [ ] **速率配置**: `rate_limit_rps` 匹配下游 API 限制
- [ ] **突发容量**: `rate_limit_burst` 合理(建议 50)
- [ ] **令牌耗尽测试**: 并发请求超限时正确限流

### 5.3 ResultCache

- [ ] **缓存大小**: `max_size` 合理(建议 1000)
- [ ] **TTL 配置**: `ttl_seconds` 合理(建议 3600)
- [ ] **命中率**: 运行后检查 cache_hit / cache_miss

### 5.4 性能基准

- [ ] **linear_10**: P50 < 30ms
- [ ] **parallel_10**: P50 < 15ms
- [ ] **large_100**: P50 < 200ms
- [ ] **无回退**: 与 baseline 对比 avg 变化 < 10%

---

## 六、分布式执行 (如启用)

- [ ] **TaskQueue**: Redis 队列可达
- [ ] **DistributedLock**: 锁获取/释放正常
- [ ] **Worker 进程**: `python -m xruntime.worker` 可启动
- [ ] **任务消费**: Worker 能从队列消费并执行
- [ ] **互斥锁**: 并发 Worker 不重复执行同一 task

---

## 七、HITL 审批

- [ ] **ApprovalStore**: 已配置(Redis 或 InMemory)
- [ ] **审批超时**: `timeout_seconds` 合理(建议 3600)
- [ ] **on_timeout 策略**: 默认 `reject`(fail-closed)
- [ ] **审批流程**: draft → approve → send 端到端测试通过
- [ ] **pending 监控**: 告警在 pending > 10 时触发

---

## 八、CI/CD

- [ ] **observability-check.yml**: CI 中告警验证通过
- [ ] **perf-benchmark.yml**: CI 中基准无回退
- [ ] **Docker 镜像**: 构建成功且通过安全扫描
- [ ] **部署脚本**: `deploy/verify_deployment.sh` 全部通过
- [ ] **回滚方案**: 已准备回滚步骤和回滚镜像

---

## 九、上线后验证

### 9.1 首次部署后 5 分钟内

- [ ] `/health` 返回 ok
- [ ] `/ready` 返回 ready
- [ ] `/metrics` 返回指标数据
- [ ] Jaeger 有 trace
- [ ] 无 critical 告警 firing

### 9.2 首次部署后 30 分钟内

- [ ] 运行 smoke test workflow → COMPLETED
- [ ] Prometheus 抓取成功(targets 全 UP)
- [ ] 审计日志写入正常
- [ ] Redis 连接正常
- [ ] 无 OOM / CrashLoopBackOff

### 9.3 首次部署后 24 小时内

- [ ] 无性能回退(P50 稳定)
- [ ] 无告警误报
- [ ] 审计日志保留正常(ELK ILM)
- [ ] Redis 内存稳定(无持续增长)

---

## 十、签批

| 角色 | 姓名 | 日期 | 签名 |
|------|------|------|------|
| 开发负责人 | | | |
| 运维负责人 | | | |
| 安全负责人 | | | |
| 产品负责人 | | | |

---

**说明**: 所有项目必须 ✅ 后方可上线。任何 ❌ 需要记录原因并获得开发负责人批准。

# P3-D 可观测性 CI/CD 集成配置

> 生成日期：2026-07-01
> 基础：P3-D WorkflowTracer + WorkflowMetrics + WorkflowAuditLogger
> 目标：将 OTel 追踪、Prometheus 指标、审计日志集成到 CI/CD 流水线

---

## 一、集成架构

```
CI/CD Pipeline
  ├── Test Runner (pytest + tracer + metrics + audit)
  ├── Coverage Check
  ├── Build Image
  └── Deploy + Smoke Test
       │
       ▼
  WorkflowTracer + WorkflowMetrics + WorkflowAuditLogger
       │                │                 │
       ▼                ▼                 ▼
   Jaeger          Prometheus         ELK Stack
  (Traces)       + Alertmanager      (Audit)
```

---

## 二、OTel 追踪 CI 集成

### 2.1 CI 环境变量

```bash
# .env.ci
XRUNTIME_OBSERVABILITY_OTEL_ENABLED=true
XRUNTIME_OBSERVABILITY_OTEL_ENDPOINT=http://localhost:4317
XRUNTIME_OBSERVABILITY_AUDIT_ENABLED=true
XRUNTIME_OBSERVABILITY_AUDIT_STORAGE=file
XRUNTIME_CREDENTIAL_BROKER_REDIS_URL=redis://localhost:6379/0
XRUNTIME_CREDENTIAL_BROKER_AUTO_ROTATE_ENABLED=true
```

### 2.2 GitHub Actions 工作流

```yaml
# .github/workflows/ci-observability.yml
name: CI with Observability
on: [push, pull_request]

jobs:
  test-with-observability:
    runs-on: ubuntu-latest
    services:
      jaeger:
        image: jaegertracing/all-in-one:1.57
        ports: ['16686:16686', '4317:4317']
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e ".[dev,xruntime-dev]"
      - name: Run tests with OTel tracing
        env:
          XRUNTIME_OBSERVABILITY_OTEL_ENABLED: "true"
          XRUNTIME_OBSERVABILITY_OTEL_ENDPOINT: "http://localhost:4317"
        run: pytest tests/xruntime --cov=xruntime --cov-report=xml --junitxml=test-results.xml
      - name: Verify traces exported
        run: curl -s "http://localhost:16686/api/traces?service=xruntime" | python -c "import sys,json;d=json.load(sys.stdin);print(f'Traces: {len(d[\"data\"])}')"
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: observability-artifacts, path: "test-results.xml\ncoverage.xml" }
```

---

## 三、自动告警规则

### 3.1 Prometheus 告警规则

```yaml
# deploy/alerts.yml
groups:
  - name: workflow_execution_alerts
    rules:
      # Workflow 失败率 > 5%
      - alert: WorkflowHighFailureRate
        expr: |
          sum(rate(workflow_step_total{status="FAILED"}[5m]))
          / sum(rate(workflow_step_total[5m])) > 0.05
        for: 5m
        labels: { severity: critical, team: platform }
        annotations:
          summary: "Workflow failure rate > 5%"
          description: "Check Jaeger for failed traces."

      # Step P95 延迟 > 5 秒
      - alert: WorkflowStepHighLatency
        expr: |
          histogram_quantile(0.95, rate(workflow_step_duration_ms_bucket[5m])) > 5000
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "Step P95 latency > 5s"
          description: "Step {{ $labels.step }} P95 = {{ $value }}ms"

      # Checkpoint 保存停止
      - alert: CheckpointSaveStopped
        expr: rate(workflow_checkpoint_save_total[5m]) == 0 and rate(workflow_step_total[5m]) > 0
        for: 5m
        labels: { severity: critical }
        annotations:
          summary: "No checkpoint saves despite active workflows"

  - name: credential_broker_alerts
    rules:
      # 凭证即将过期
      - alert: CredentialExpiringSoon
        expr: xruntime_credential_expiry_seconds < 300
        for: 1m
        labels: { severity: warning, team: security }
        annotations:
          summary: "Credential expiring in < 5 min"

      # AutoRotation 失败
      - alert: AutoRotationFailure
        expr: rate(xruntime_auto_rotation_failures_total[5m]) > 0
        for: 2m
        labels: { severity: critical, team: security }
        annotations:
          summary: "Auto-rotation failure detected"

  - name: approval_alerts
    rules:
      # 审批超时
      - alert: ApprovalTimeout
        expr: xruntime_approval_timed_out_total > 0
        for: 1m
        labels: { severity: warning, team: ops }
        annotations:
          summary: "Approval request timed out"

      # 待审批积压
      - alert: ApprovalBacklog
        expr: xruntime_approval_pending_count > 10
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "Approval backlog > 10"

  - name: system_resource_alerts
    rules:
      # Redis 连接池耗尽
      - alert: RedisPoolExhausted
        expr: |
          xruntime_redis_pool_active / xruntime_redis_pool_max > 0.9
        for: 2m
        labels: { severity: critical }
        annotations:
          summary: "Redis connection pool > 90%"

      # OTel Collector 内存高
      - alert: OtelCollectorHighMemory
        expr: process_resident_memory_bytes{job="otel-collector"} > 512000000
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "OTel Collector memory > 512MB"
```

### 3.2 Alertmanager 路由

```yaml
# deploy/alertmanager.yml
route:
  receiver: 'default'
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

receivers:
  - name: 'default'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/...'
        channel: '#xruntime-alerts'
        send_resolved: true

  - name: 'critical'
    webhook_configs:
      - url: 'http://pagerduty:5000/critical'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/...'
        channel: '#xruntime-critical'
        send_resolved: true

routes:
  - match: { severity: critical }
    receiver: 'critical'
    group_wait: 10s
    repeat_interval: 1h
  - match: { severity: warning }
    receiver: 'default'
```

---

## 四、Docker Compose 完整观测栈

```yaml
# deploy/docker-compose.observability.yml
version: '3.8'
services:
  xruntime:
    build: .
    ports: ['8900:8900']
    environment:
      XRUNTIME_OBSERVABILITY_OTEL_ENABLED: "true"
      XRUNTIME_OBSERVABILITY_OTEL_ENDPOINT: "http://otel-collector:4317"
      XRUNTIME_OBSERVABILITY_AUDIT_ENABLED: "true"
      XRUNTIME_CREDENTIAL_BROKER_REDIS_URL: "redis://redis:6379/0"
    depends_on: [redis, otel-collector]

  redis:
    image: redis:7-alpine
    ports: ['6379:6379']

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.103.0
    ports: ['4317:4317', '8889:8889']
    volumes:
      - ./otel-collector-config.yaml:/etc/otelcol/config.yaml

  jaeger:
    image: jaegertracing/all-in-one:1.57
    ports: ['16686:16686']
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

  prometheus:
    image: prom/prometheus:v2.52.0
    ports: ['9090:9090']
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - ./alerts.yml:/etc/prometheus/alerts.yml

  alertmanager:
    image: prom/alertmanager:v0.27.0
    ports: ['9093:9093']
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
```

---

## 五、告警规则汇总

| 告警名 | 条件 | 严重度 | 团队 |
|--------|------|--------|------|
| WorkflowHighFailureRate | 失败率 > 5% (5min) | critical | platform |
| WorkflowStepHighLatency | P95 > 5s (10min) | warning | platform |
| CheckpointSaveStopped | 无 checkpoint 保存 (5min) | critical | platform |
| CredentialExpiringSoon | 凭证 < 5min 过期 | warning | security |
| AutoRotationFailure | 轮换失败 (2min) | critical | security |
| ApprovalTimeout | 审批超时 | warning | ops |
| ApprovalBacklog | 待审批 > 10 (5min) | warning | ops |
| RedisPoolExhausted | 连接池 > 90% (2min) | critical | platform |
| OtelCollectorHighMemory | 内存 > 512MB (5min) | warning | platform |

---

## 六、部署步骤

1. **启动观测栈**: `docker compose -f deploy/docker-compose.observability.yml up -d`
2. **验证 Jaeger**: 访问 `http://localhost:16686` 查看 traces
3. **验证 Prometheus**: 访问 `http://localhost:9090/targets` 确认抓取
4. **验证告警**: 访问 `http://localhost:9093/api/v2/alerts` 查看活跃告警
5. **运行冒烟测试**: `pytest tests/xruntime -q` 确认 traces + metrics + audit 正常生成

# HPA / PDB 配置审查报告 — 1000+ 并发场景

> 审查日期: 2026-07-01
> 审查文件: deploy/k8s-xruntime-production.yaml
> 审查结论: **需要 3 处修正**

---

## 一、HPA 审查

### 1.1 当前配置

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  minReplicas: 5
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: {type: Utilization, averageUtilization: 70}
    - type: Resource
      resource:
        name: memory
        target: {type: Utilization, averageUtilization: 80}
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies: [{type: Percent, value: 50, periodSeconds: 60}]
    scaleDown:
      stabilizationWindowSeconds: 300
      policies: [{type: Percent, value: 20, periodSeconds: 60}]
```

### 1.2 发现的问题

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | minReplicas=5 过低 | **高** | 1000 并发场景下,5 副本仅支持 250 并发(50/副本)。流量突增时扩容需要时间,导致请求积压。应设为 10。 |
| 2 | 缺少 Pods 指标 | **中** | 仅用 CPU/内存指标,无法根据并发请求数(RPS)自动伸缩。应添加 `Pods` 指标基于 `workflow_step_total` 速率。 |
| 3 | scaleUp 窗口太短 | **中** | 30s 稳定窗口可能导致频繁抖动。流量突增时应更快扩容,但 30s 可能导致过早缩容又扩容。 |

### 1.3 修正后的 HPA 配置

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: xruntime-api-hpa
  namespace: xruntime
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: xruntime-api
  minReplicas: 10                    # ← 修正: 10 副本保底(1000 并发)
  maxReplicas: 30                    # ← 修正: 上限提升到 30(应对突发)
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 65      # ← 修正: 65%(更早触发扩容)
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 75      # ← 修正: 75%
    # ← 新增: 基于 QPS 的自定义指标
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: "50"          # 每副本 50 RPS → 扩容
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0   # ← 修正: 0s(立即扩容)
      selectPolicy: Max
      policies:
        - type: Percent
          value: 100                  # 最多翻倍
          periodSeconds: 30
        - type: Pods
          value: 5                    # 每次最多加 5 Pod
          periodSeconds: 30
    scaleDown:
      stabilizationWindowSeconds: 300 # 5 分钟稳定后缩容
      selectPolicy: Min
      policies:
        - type: Percent
          value: 10                  # 每次最多缩 10%
          periodSeconds: 60
```

### 1.4 容量计算(修正后)

| 场景 | 副本数 | 每副本并发 | 总并发 |
|------|--------|-----------|--------|
| 最小(minReplicas) | 10 | 50 | 500 |
| 正常(CPU 65%) | 12 | 50 | 600 |
| 峰值(CPU 85%) | 20 | 50 | 1000 |
| 最大(maxReplicas) | 30 | 50 | 1500 |
| + 5 Worker | 5 | 20 | 100 |
| **峰值总并发** | — | — | **1100+** |

---

## 二、PDB 审查

### 2.1 当前配置

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
spec:
  minAvailable: 3
  selector:
    matchLabels: {app: xruntime-api}
```

### 2.2 发现的问题

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | minAvailable=3 过低 | **高** | 10 副本时仅保证 3 个可用(70% 可被驱逐)。滚动更新时可能同时 7 个不可用 → 仅 3 副本承载 1000 并发 = 严重降质。应改为 `maxUnavailable: 1`。 |
| 2 | 缺少 Worker PDB | **中** | Worker Deployment 无 PDB,节点维护时可能全部驱逐。 |

### 2.3 修正后的 PDB 配置

```yaml
# API PDB — 修正: 使用 maxUnavailable 替代 minAvailable
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: xruntime-api-pdb
  namespace: xruntime
spec:
  maxUnavailable: 1                  # ← 修正: 最多 1 个不可用(而非最少 3 个)
  selector:
    matchLabels:
      app: xruntime-api

---
# ← 新增: Worker PDB
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: xruntime-worker-pdb
  namespace: xruntime
spec:
  maxUnavailable: 1                  # 最多 1 个 worker 不可用
  selector:
    matchLabels:
      app: xruntime-worker
```

### 2.4 滚动更新影响(修正后)

| 场景 | 当前配置 | 修正后 |
|------|---------|--------|
| 10 副本滚动更新 | 最多 7 个不可用(仅 3 可用) | 最多 1 个不可用(9 可用) |
| 1000 并发时更新 | 300 并发(严重降质) | 900 并发(10% 降质) |
| 节点维护驱逐 | 可驱逐 7 个 | 最多驱逐 1 个 |

---

## 三、其他 K8s 配置审查

### 3.1 Deployment 健康检查

```yaml
livenessProbe:
  httpGet: {path: /health, port: 8900}
  initialDelaySeconds: 10
  periodSeconds: 30
  timeoutSeconds: 5                # ← OK
readinessProbe:
  httpGet: {path: /ready, port: 8900}
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 3                # ← OK
  # 建议添加 failureThreshold: 3
```

**建议**: readinessProbe 添加 `failureThreshold: 3`(3 次失败才标记不可用)。

### 3.2 优雅关闭

```yaml
terminationGracePeriodSeconds: 60  # ← OK(足够等待 workflow 完成)
lifecycle:
  preStop:
    exec:
      command: ["python", "-c", "import asyncio; asyncio.sleep(5)"]
```

**建议**: `preStop` 中的 sleep 应改为等待 XRuntime 的 `/health` 返回 not-ready 后再退出。

### 3.3 资源限制

```yaml
resources:
  requests: {cpu: "500m", memory: "512Mi"}    # ← OK
  limits:   {cpu: "1000m", memory: "1Gi"}      # ← OK
```

**验证**: 10 Pod × 1 CPU = 10 CPU, 10 Pod × 1GB = 10GB。需要节点有足够资源。

---

## 四、审查结论

| 项目 | 修正前 | 修正后 | 影响 |
|------|--------|--------|------|
| HPA minReplicas | 5 | **10** | 保底 500 并发 |
| HPA maxReplicas | 20 | **30** | 峰值 1500 并发 |
| HPA scaleUp 窗口 | 30s | **0s** | 立即扩容 |
| HPA QPS 指标 | 无 | **新增** | 基于 RPS 扩容 |
| PDB 策略 | minAvailable:3 | **maxUnavailable:1** | 滚动更新 90% 可用 |
| Worker PDB | 无 | **新增** | Worker 保护 |

**审查状态**: 3 处高优先级修正 + 3 处新增配置。修正后可支撑 1000+ 并发。

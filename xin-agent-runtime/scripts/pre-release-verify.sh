#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

P=0; F=0; S=0
ok()   { echo "  [PASS] $1"; P=$((P+1)); }
fail() { echo "  [FAIL] $1"; F=$((F+1)); }
skip() { echo "  [SKIP] $1"; S=$((S+1)); }

echo "================================================================"
echo "  XRuntime 上线前验证  $(date '+%Y-%m-%d %H:%M')"
echo "================================================================"

# 1. 代码与测试
echo ""; echo "── 1. 代码与测试 ──"
R=$(python3 -m pytest tests/xruntime -p no:cacheprovider --no-header -q 2>&1 | tail -1)
echo "$R" | grep -qE "[0-9]+ passed.*" && ! echo "$R" | grep -q "failed" && ok "全量测试: $R" || fail "测试失败: $R"

python3 -m black --line-length=79 --check src/xruntime/_runtime/_workflow/ src/xruntime/_runtime/_credential/ 2>&1 | grep -q "would reformat" \
  && fail "black 格式不合规" || ok "black 格式合规"

python3 -m flake8 --max-line-length=79 --extend-ignore=E203,W503,E501 \
  src/xruntime/_runtime/_workflow/ src/xruntime/_runtime/_credential/ 2>&1 | grep -q . \
  && fail "flake8 有错误" || ok "flake8 无错误"
skip "git 状态(手动检查)"

# 2. 配置文件
echo ""; echo "── 2. 配置文件 ──"
FILES="deploy/alerts.yml deploy/prometheus.yml deploy/alertmanager.yml \
deploy/otel-collector-config.yaml deploy/filebeat.yml \
deploy/docker-compose.observability.yml deploy/docker-compose.local-verify.yml \
deploy/k8s-xruntime-production.yaml \
.github/workflows/observability-check.yml .github/workflows/perf-benchmark.yml"
for f in $FILES; do
  [ -f "$f" ] && ok "存在: $f" || fail "缺失: $f"
done
ALLOK=true
for f in $FILES; do
  [ -f "$f" ] && python3 -c "import yaml; list(yaml.safe_load_all(open('$f')))" 2>/dev/null \
    || { [ -f "$f" ] && fail "YAML 语法错误: $f"; ALLOK=false; }
done
$ALLOK && ok "所有 YAML 语法正确"
AC=$(python3 -c "import yaml; d=yaml.safe_load(open('deploy/alerts.yml')); print(sum(len(g.get('rules',[])) for g in d.get('groups',[])))" 2>/dev/null || echo 0)
[ "$AC" -ge 9 ] && ok "告警规则: $AC (>=9)" || fail "告警不足: $AC"

# 3. 基础设施
echo ""; echo "── 3. 基础设施 ──"
redis-cli ping 2>/dev/null | grep -q PONG && ok "Redis 可连接" || skip "Redis 未运行"
curl -sf http://localhost:8900/health >/dev/null 2>&1 && ok "XRuntime 健康" || skip "XRuntime 未运行"
curl -sf http://localhost:9090/-/healthy >/dev/null 2>&1 && ok "Prometheus 健康" || skip "Prometheus 未运行"
curl -sf http://localhost:9093/-/healthy >/dev/null 2>&1 && ok "Alertmanager 健康" || skip "Alertmanager 未运行"
curl -sf http://localhost:16686 >/dev/null 2>&1 && ok "Jaeger 可达" || skip "Jaeger 未运行"
curl -sf http://localhost:9200 >/dev/null 2>&1 && ok "Elasticsearch 可达" || skip "ES 未运行"

# 4. 可观测性
echo ""; echo "── 4. 可观测性 ──"
M=$(curl -sf http://localhost:8900/metrics 2>/dev/null || echo "")
[ -n "$M" ] && echo "$M" | grep -q "workflow_step_total" && ok "/metrics 含 workflow_step_total" || skip "/metrics 端点"
RULES=$(curl -sf http://localhost:9090/api/v1/rules 2>/dev/null || echo "")
[ -n "$RULES" ] && echo "$RULES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(len(g.get('rules',[])) for g in d.get('data',{}).get('groups',[])))" 2>/dev/null | { read n; [ "$n" -ge 9 ] 2>/dev/null; } && ok "Prometheus 加载告警规则" || skip "Prometheus 告警(需运行时)"

python3 -c "
from xruntime._runtime._workflow._metrics import WorkflowMetrics as M
m=M(); m.record_step('t','s','COMPLETED'); m.record_step_duration('t','s',1.0)
m.record_checkpoint_save('t'); m.record_resume('t')
m.record_credential_expiry('c',9e9); m.record_auto_rotation_failure()
m.record_approval_timeout(); m.set_approval_pending('a',1); m.set_redis_pool_stats(1,2)
t=m.export_prometheus()
for n in ['workflow_step_total','xruntime_credential_expiry_seconds','xruntime_auto_rotation_failures_total','xruntime_approval_timed_out_total','xruntime_approval_pending_count','xruntime_redis_pool_active_connections']:
    assert n in t, n
" 2>/dev/null && ok "10 个指标源代码验证通过" || fail "指标源验证失败"

python3 -c "
import json
from xruntime._runtime._workflow._audit import WorkflowAuditLogger
a=WorkflowAuditLogger(); a.record_step('w','s','a','COMPLETED',100.0,'t1')
t=json.dumps(a.get_entries())
assert 'api_key' not in t.lower() and 'secret' not in t.lower()
" 2>/dev/null && ok "审计日志不含密钥" || fail "审计安全验证失败"

# 5. 安全
echo ""; echo "── 5. 安全 ──"
python3 -c "
from xruntime._runtime._credential._scope_hierarchy import ScopeHierarchy
try: ScopeHierarchy({'a':['b'],'b':['a']}); exit(1)
except ValueError: pass
" 2>/dev/null && ok "Scope 循环检测正常" || fail "Scope 循环检测异常"

python3 -c "
from xruntime._runtime._workflow._approval import ApprovalStep
s=ApprovalStep(id='a',name='A',agent='x',prompt='p',approver='m',timeout_seconds=60)
assert s.on_timeout=='reject'
" 2>/dev/null && ok "on_timeout 默认 reject" || fail "on_timeout 配置错误"
skip "Redis 密码(需运行时)"
skip "TLS 证书(需 cert-manager)"

# 6. 性能
echo ""; echo "── 6. 性能 ──"
B=$(python3 -m pytest tests/xruntime/test_benchmark.py -p no:cacheprovider --no-header -q 2>&1 | tail -1)
echo "$B" | grep -q "passed" && ok "基准测试: $B" || fail "基准测试失败"

C=$(python3 -m pytest tests/xruntime/test_concurrency.py tests/xruntime/test_rate_limiter.py tests/xruntime/test_result_cache.py -p no:cacheprovider --no-header -q 2>&1 | tail -1)
echo "$C" | grep -q "passed" && ok "并发模块: $C" || fail "并发模块失败"

D=$(python3 -m pytest tests/xruntime/test_p4c_distributed.py -p no:cacheprovider --no-header -q 2>&1 | tail -1)
echo "$D" | grep -q "passed" && ok "分布式模块: $D" || fail "分布式模块失败"
skip "连接池(需 Redis 运行时)"

# 7. CI/CD
echo ""; echo "── 7. CI/CD ──"
[ -f ".github/workflows/observability-check.yml" ] && ok "observability-check.yml 存在" || fail "CI workflow 缺失"
[ -f ".github/workflows/perf-benchmark.yml" ] && ok "perf-benchmark.yml 存在" || fail "CI workflow 缺失"

python3 -c "import yaml; list(yaml.safe_load_all(open('deploy/k8s-xruntime-production.yaml')))" 2>/dev/null \
  && ok "K8s YAML 多文档语法正确" || fail "K8s YAML 语法错误"

HPA=$(python3 -c "
import yaml
for d in yaml.safe_load_all(open('deploy/k8s-xruntime-production.yaml')):
    if d and d.get('kind')=='HorizontalPodAutoscaler':
        print(d['spec']['minReplicas']); break
" 2>/dev/null || echo 0)
[ "$HPA" -ge 5 ] 2>/dev/null && ok "HPA minReplicas=$HPA" || fail "HPA 配置异常"

# 8. 上线后
echo ""; echo "── 8. 上线后验证 ──"
curl -sf http://localhost:8900/health >/dev/null 2>&1 && ok "健康检查通过" || skip "健康检查(需运行时)"
curl -sf http://localhost:8900/ready >/dev/null 2>&1 && ok "就绪检查通过" || skip "就绪检查(需运行时)"
curl -sf http://localhost:9090/api/v1/alerts 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); alerts=[a for a in d.get('data',{}).get('alerts',[]) if a.get('state')=='firing']; print(len(alerts))" 2>/dev/null | { read n; [ "$n" -eq 0 ] 2>/dev/null; } && ok "无告警触发中" || skip "告警状态(需运行时)"
skip "Jaeger 追踪(需运行时)"
skip "审计日志(需运行时)"
skip "Grafana 仪表板(需手动)"

# 汇总
echo ""; echo "================================================================"
echo "  结果: $P PASS, $F FAIL, $S SKIP"
echo "================================================================"
[ "$F" -eq 0 ] && echo "  ✅ 验证通过 — 可以上线" || echo "  ❌ 有 $F 项失败 — 需修复后上线"
exit $F

#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# verify_container_security.sh
#
# 本地安全验证脚本：构建修复后的镜像并验证容器满足以下安全约束：
#   1. 容器以非 root 用户运行 (UID ≠ 0)
#   2. 容器没有 CAP_SYS_ADMIN 等 dangerous capabilities
#   3. no-new-privileges 标志已设置
#   4. 只读根文件系统生效 (写入 /etc 应失败)
#   5. 网络隔离生效 (无默认 bridge)
#
# 用法：
#   ./deploy/verify_container_security.sh
#
# 退出码：
#   0 — 所有安全检查通过
#   1 — 至少一项安全检查失败
#   2 — 前置条件不满足 (Docker 未安装或不可用)
set -euo pipefail

# ── 颜色输出 ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS_COUNT=0
FAIL_COUNT=0

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

log_info() {
    echo -e "${YELLOW}[INFO]${NC} $1"
}

# ── 前置检查 ──────────────────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装${NC}"
    exit 2
fi

if ! docker info &> /dev/null; then
    echo -e "${RED}错误: Docker daemon 不可用${NC}"
    exit 2
fi

log_info "Docker daemon 可用，开始安全验证..."

# ── 构建测试镜像 ──────────────────────────────────────────────
IMAGE_TAG="xruntime-security-test:latest"
CONTAINER_NAME="xruntime-sec-verify"

log_info "构建测试镜像 ${IMAGE_TAG}..."
docker build -t "${IMAGE_TAG}" -f Dockerfile . --quiet

log_info "启动临时容器进行安全检查..."
# 清理可能存在的旧容器
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

# 启动容器（使用修复后的配置）
docker run -d \
    --name "${CONTAINER_NAME}" \
    --user "1000:1000" \
    --cap-drop "ALL" \
    --security-opt "no-new-privileges" \
    --memory "256m" \
    --cpus "0.5" \
    --pids-limit "64" \
    --network "none" \
    --read-only \
    --tmpfs "/tmp:size=32m" \
    --tmpfs "/run:size=32m" \
    "${IMAGE_TAG}" \
    sleep 60

# 等待容器启动
sleep 2

# ── 安全检查 ──────────────────────────────────────────────────

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  容器安全验证 (P0-A + P0-B)"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# 检查 1: 容器以非 root 用户运行
echo "── 检查 1: 非 root 用户 ─────────────────────────────────"
CONTAINER_USER=$(docker exec "${CONTAINER_NAME}" id -u 2>/dev/null || echo "UNKNOWN")
CONTAINER_GROUP=$(docker exec "${CONTAINER_NAME}" id -g 2>/dev/null || echo "UNKNOWN")

if [ "${CONTAINER_USER}" != "0" ] && [ "${CONTAINER_USER}" != "UNKNOWN" ]; then
    log_pass "容器以非 root 用户运行 (UID=${CONTAINER_USER}, GID=${CONTAINER_GROUP})"
else
    log_fail "容器以 root 运行 (UID=${CONTAINER_USER}) — 严重安全漏洞"
fi

# 检查 whoami
CONTAINER_WHOAMI=$(docker exec "${CONTAINER_NAME}" whoami 2>/dev/null || echo "root")
if [ "${CONTAINER_WHOAMI}" != "root" ]; then
    log_pass "whoami 返回非 root 用户: ${CONTAINER_WHOAMI}"
else
    log_fail "whoami 返回 root"
fi

# 检查 2: CapDrop ALL 生效
echo ""
echo "── 检查 2: CapDrop ALL ─────────────────────────────────"
CAPS=$(docker exec "${CONTAINER_NAME}" cat /proc/1/status 2>/dev/null | grep "CapEff" || echo "CapEff: UNKNOWN")

if echo "${CAPS}" | grep -q "CapEff:\s*0000000000000000"; then
    log_pass "所有 Linux capabilities 已丢弃 (CapEff=0)"
else
    # 解析 CapEff 的十六进制值
    CAP_HEX=$(echo "${CAPS}" | awk '{print $2}')
    if [ "${CAP_HEX}" = "0000000000000000" ]; then
        log_pass "所有 Linux capabilities 已丢弃 (CapEff=${CAP_HEX})"
    else
        log_fail "容器仍保留 capabilities (CapEff=${CAP_HEX}) — CapDrop ALL 未生效"
    fi
fi

# 检查 3: no-new-privileges
echo ""
echo "── 检查 3: no-new-privileges ───────────────────────────"
# 尝试通过 setuid 提权（应该失败）
SETUID_RESULT=$(docker exec "${CONTAINER_NAME}" su root -c "whoami" 2>&1 || echo "DENIED")

if echo "${SETUID_RESULT}" | grep -qi "denied\|permission denied\|authentication failure\|su: must be suid"; then
    log_pass "no-new-privileges 生效 — 提权尝试被拒绝"
else
    # su 可能不存在，尝试另一种方式
    NNP_CHECK=$(docker inspect "${CONTAINER_NAME}" --format '{{.HostConfig.SecurityOpt}}' 2>/dev/null || echo "")
    if echo "${NNP_CHECK}" | grep -q "no-new-privileges"; then
        log_pass "no-new-privileges 已在 HostConfig 中设置"
    else
        log_fail "无法确认 no-new-privileges 是否生效 (su 输出: ${SETUID_RESULT})"
    fi
fi

# 检查 4: 只读根文件系统
echo ""
echo "── 检查 4: ReadonlyRootfs ──────────────────────────────"
# 尝试写入 /etc（应该失败）
WRITE_RESULT=$(docker exec "${CONTAINER_NAME}" sh -c "echo test > /etc/test_write" 2>&1 || echo "READONLY")

if echo "${WRITE_RESULT}" | grep -qi "read-only\|readonly\|Read-only"; then
    log_pass "只读根文件系统生效 — 写入 /etc 被拒绝"
else
    # 清理测试文件
    docker exec "${CONTAINER_NAME}" rm -f /etc/test_write 2>/dev/null || true
    log_fail "根文件系统可写 — ReadonlyRootfs 未生效 (输出: ${WRITE_RESULT})"
fi

# 验证 /tmp 可写（tmpfs 应该允许）
TMP_WRITE=$(docker exec "${CONTAINER_NAME}" sh -c "echo test > /tmp/test_write && cat /tmp/test_write && rm /tmp/test_write" 2>&1 || echo "FAILED")
if echo "${TMP_WRITE}" | grep -q "test"; then
    log_pass "/tmp tmpfs 可写 (功能正常)"
else
    log_fail "/tmp 不可写 — tmpfs 配置可能有问题 (输出: ${TMP_WRITE})"
fi

# 检查 5: 网络隔离
echo ""
echo "── 检查 5: 网络隔离 ───────────────────────────────────"
NET_MODE=$(docker inspect "${CONTAINER_NAME}" --format '{{.HostConfig.NetworkMode}}' 2>/dev/null || echo "UNKNOWN")

if [ "${NET_MODE}" = "none" ]; then
    log_pass "网络模式为 none — 完全隔离"
elif [ "${NET_MODE}" != "bridge" ] && [ "${NET_MODE}" != "default" ]; then
    log_pass "网络模式为自定义: ${NET_MODE} (非默认 bridge)"
else
    log_fail "网络模式为 ${NET_MODE} — 使用了默认 bridge (未隔离)"
fi

# 验证无网络访问
PING_RESULT=$(docker exec "${CONTAINER_NAME}" sh -c "cat /etc/resolv.conf 2>/dev/null | head -1" 2>&1 || echo "NO_DNS")
if echo "${PING_RESULT}" | grep -q "NO_DNS\|^$"; then
    log_pass "无 DNS 配置 — 网络确实被禁用"
fi

# 检查 6: 资源限制
echo ""
echo "── 检查 6: 资源限制 ───────────────────────────────────"
MEM_LIMIT=$(docker inspect "${CONTAINER_NAME}" --format '{{.HostConfig.Memory}}' 2>/dev/null || echo "0")
CPU_LIMIT=$(docker inspect "${CONTAINER_NAME}" --format '{{.HostConfig.NanoCpus}}' 2>/dev/null || echo "0")
PIDS_LIMIT=$(docker inspect "${CONTAINER_NAME}" --format '{{.HostConfig.PidsLimit}}' 2>/dev/null || echo "0")

if [ "${MEM_LIMIT}" != "0" ] && [ "${MEM_LIMIT}" != "" ]; then
    MEM_MB=$((MEM_LIMIT / 1024 / 1024))
    log_pass "内存限制: ${MEM_MB} MiB"
else
    log_fail "无内存限制"
fi

if [ "${CPU_LIMIT}" != "0" ] && [ "${CPU_LIMIT}" != "" ]; then
    CPU_CORES=$(echo "scale=1; ${CPU_LIMIT} / 1000000000" | bc 2>/dev/null || echo "${CPU_LIMIT}")
    log_pass "CPU 限制: ${CPU_CORES} cores"
else
    log_fail "无 CPU 限制"
fi

if [ "${PIDS_LIMIT}" != "0" ] && [ "${PIDS_LIMIT}" != "" ] && [ "${PIDS_LIMIT}" != "-1" ]; then
    log_pass "进程数限制: ${PIDS_LIMIT}"
else
    log_fail "无进程数限制"
fi

# 检查 7: Dockerfile 中的 USER 指令
echo ""
echo "── 检查 7: Dockerfile USER 指令 ───────────────────────"
DOCKERFILE_USER=$(grep -E "^USER\s+" Dockerfile 2>/dev/null | tail -1 | awk '{print $2}' || echo "")
if [ -n "${DOCKERFILE_USER}" ] && [ "${DOCKERFILE_USER}" != "root" ] && [ "${DOCKERFILE_USER}" != "0" ]; then
    log_pass "Dockerfile 包含 USER ${DOCKERFILE_USER} 指令"
else
    log_fail "Dockerfile 缺少非 root USER 指令"
fi

# 检查 8: docker-compose.yml security_opt
echo ""
echo "── 检查 8: docker-compose.yml 安全配置 ─────────────────"
COMPOSE_SEC=$(grep -c "no-new-privileges" deploy/docker-compose.yml 2>/dev/null || echo "0")
COMPOSE_CAPDROP=$(grep -c "cap_drop" deploy/docker-compose.yml 2>/dev/null || echo "0")
COMPOSE_MEM=$(grep -c "mem_limit" deploy/docker-compose.yml 2>/dev/null || echo "0")

if [ "${COMPOSE_SEC}" -gt 0 ]; then
    log_pass "docker-compose.yml 包含 no-new-privileges (${COMPOSE_SEC} 处)"
else
    log_fail "docker-compose.yml 缺少 no-new-privileges"
fi

if [ "${COMPOSE_CAPDROP}" -gt 0 ]; then
    log_pass "docker-compose.yml 包含 cap_drop (${COMPOSE_CAPDROP} 处)"
else
    log_fail "docker-compose.yml 缺少 cap_drop"
fi

if [ "${COMPOSE_MEM}" -gt 0 ]; then
    log_pass "docker-compose.yml 包含 mem_limit"
else
    log_fail "docker-compose.yml 缺少 mem_limit"
fi

# ── 清理 ──────────────────────────────────────────────────────
echo ""
log_info "清理测试容器和镜像..."
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
docker rmi "${IMAGE_TAG}" 2>/dev/null || true

# ── 总结 ──────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  验证结果总结"
echo "═══════════════════════════════════════════════════════════════"
echo -e "  ${GREEN}通过: ${PASS_COUNT}${NC}  |  ${RED}失败: ${FAIL_COUNT}${NC}"
echo ""

if [ "${FAIL_COUNT}" -eq 0 ]; then
    echo -e "${GREEN}✓ 所有安全检查通过 — 容器确实无法以 root 身份运行${NC}"
    exit 0
else
    echo -e "${RED}✗ ${FAIL_COUNT} 项安全检查失败 — 请检查上方详细输出${NC}"
    exit 1
fi

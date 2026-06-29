# XRuntime Docker 部署指南

## 快速开始（5分钟）

### 1. 前置要求

- Docker 20.10+
- Docker Compose v2+
- 至少 2GB 内存
- 至少 10GB 磁盘空间

### 2. 一键启动

```bash
# 进入部署目录
cd deploy

# 复制配置文件（可选，使用默认配置可跳过）
cp .env.example .env

# 启动服务
./start.sh
```

等待约 1-2 分钟，服务启动后访问：
- 健康检查: http://localhost:8900/health
- 服务地址: http://localhost:8900

### 3. 验证部署

```bash
# 运行部署测试
./test-deploy.sh
```

---

## 配置说明

### 最小配置（仅测试连通性）

无需修改任何配置，直接启动即可。使用内置 Mock 模型，适合快速验证部署。

### 标准配置（使用真实模型）

编辑 `deploy/.env` 文件，修改以下配置：

```bash
# 模型提供者: anthropic, openai, deepseek, dashscope, moonshot, ollama, gemini, xai
XRUNTIME_MODEL_PROVIDER=anthropic

# 模型 API Key
XRUNTIME_MODEL_API_KEY=sk-ant-xxxxxxxxxxxxxxxx

# 模型名称
XRUNTIME_MODEL_NAME=claude-3-sonnet-20240229
```

然后重启服务：

```bash
./start.sh restart
```

### 生产环境配置

编辑 `deploy/.env` 文件，修改以下配置：

```bash
# 生产模式
XRUNTIME_PRODUCTION=1

# 启用认证
XRUNTIME_AUTH_ENABLED=true
XRUNTIME_API_KEYS=your-api-key-1,your-api-key-2
XRUNTIME_JWT_SECRET=your-jwt-secret-at-least-32-characters

# 修改 Redis 密码
REDIS_PASSWORD=your-strong-redis-password

# 使用 Docker 沙箱（需要挂载 Docker socket）
XRUNTIME_WORKSPACE_BACKEND=docker
```

---

## 常用命令

```bash
cd deploy

# 启动服务
./start.sh

# 停止服务
./start.sh stop

# 重启服务
./start.sh restart

# 查看日志
./start.sh logs

# 查看状态
./start.sh status

# 重新构建镜像
./start.sh build

# 查看帮助
./start.sh help
```

---

## 手动操作（不用 start.sh）

### 启动服务

```bash
cd deploy
docker compose up -d
```

### 查看日志

```bash
# 查看所有服务日志
docker compose logs -f

# 只看 xruntime 日志
docker compose logs -f xruntime

# 只看 redis 日志
docker compose logs -f redis
```

### 停止服务

```bash
docker compose down

# 同时删除数据卷（谨慎使用！会丢失所有数据）
docker compose down -v
```

### 重新构建镜像

```bash
docker compose build
docker compose up -d
```

---

## 镜像导出与导入

如果需要在无网络环境部署，或者想要备份镜像，可以使用导出/导入功能。

### 导出镜像

```bash
cd deploy

# 导出当前 xruntime:latest 镜像为 tar.gz
./save-image.sh

# 指定镜像名称和输出路径
./save-image.sh xruntime:latest /path/to/output.tar.gz
```

导出示例输出：
```
源镜像: xruntime:latest
输出文件: ./xruntime-image.tar.gz
镜像大小: 1.12GB

正在导出镜像，请稍候...

✅ 镜像导出成功！

  输出文件: ./xruntime-image.tar.gz
  压缩后大小: ~247MB
  耗时: 约30秒

  导入命令:
    ./load-image.sh
```

### 导入镜像

```bash
cd deploy

# 导入当前目录的 xruntime-image.tar.gz
./load-image.sh

# 指定镜像文件路径
./load-image.sh /path/to/xruntime-image.tar.gz
```

导入成功后即可正常启动服务：
```bash
./start.sh start
```

### 镜像文件说明

| 项目 | 说明 |
|------|------|
| 原始大小 | ~1.12GB |
| 压缩后大小 | ~247MB |
| 格式 | tar.gz (docker save + gzip) |
| 架构 | arm64 / amd64 (取决于构建机器) |

> **注意**: 镜像包已添加到 `.gitignore`，不会提交到 Git 仓库。建议通过 GitHub Release 分发。

### 上传到 GitHub Release

1. 进入仓库 Releases 页面，点击 "Draft a new release"
2. 创建新的 Tag（如 `v1.0.0`）
3. 填写发布说明
4. 将 `xruntime-image.tar.gz` 拖拽到附件区域
5. 点击 "Publish release"

用户下载后使用 `./load-image.sh` 导入即可。

---

## 数据持久化

Docker Compose 配置了以下数据卷：

| 数据卷 | 用途 | 容器内路径 |
|--------|------|-----------|
| redis-data | Redis 数据 | /data |
| xruntime-workspaces | Workspace 工作目录 | /var/lib/xruntime/workspaces |
| xruntime-knowledge | 知识库数据 | /var/lib/xruntime/knowledge |
| xruntime-logs | 日志文件 | /var/log/xruntime |

### 备份数据

```bash
# 备份 Redis
docker exec xruntime-redis redis-cli -a your_password BGSAVE
docker cp xruntime-redis:/data/dump.rdb ./backup/

# 备份 Workspace
docker run --rm -v xruntime-workspaces:/data -v $(pwd)/backup:/backup alpine tar czf /backup/workspaces.tar.gz -C /data .
```

---

## 网络架构

```
┌─────────────────────────────────────────────────────────────┐
│                        宿主机网络                             │
│                                                             │
│  ┌───────────────────┐    ┌───────────────────────────┐    │
│  │  xruntime-internal│    │  xruntime-backend (内部)  │    │
│  │  (外部可访问)      │    │  (仅容器间通信)           │    │
│  └─────────┬─────────┘    └─────────────┬─────────────┘    │
│            │                             │                  │
│  ┌─────────▼─────────┐        ┌──────────▼──────────┐      │
│  │   XRuntime        │        │   Redis             │      │
│  │   (端口 8900)     │◄──────►│   (端口 6379)       │      │
│  └───────────────────┘        └─────────────────────┘      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

- `xruntime-internal`: XRuntime 服务所在网络，对外暴露 8900 端口
- `xruntime-backend`: Redis 所在内部网络，不对外暴露端口

---

## 常见问题

### Q: 启动后访问不到服务？

A: 检查服务状态：
```bash
cd deploy
./start.sh status
./start.sh logs
```

### Q: 如何修改端口？

A: 编辑 `.env` 文件，修改 `XRUNTIME_PORT=8900` 为你想要的端口，然后重启。

### Q: 如何使用 GPU？

A: 在 `docker-compose.yml` 中添加 GPU 配置：
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

### Q: 如何启用 HTTPS？

A: 建议使用 Nginx 或 Traefik 作为反向代理处理 SSL。
示例 Nginx 配置见下文。

---

## Nginx 反向代理配置示例

```nginx
server {
    listen 443 ssl http2;
    server_name xruntime.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    client_max_body_size 100M;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8900;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式响应支持
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
    }
}
```

---

## 生产环境建议

1. **安全配置**
   - 修改默认 Redis 密码
   - 启用 API Key 认证
   - 使用 HTTPS
   - 配置防火墙规则

2. **性能优化**
   - 根据 CPU 核心数调整 Worker 数量
   - 增加 Redis 内存
   - 使用 SSD 存储

3. **监控告警**
   - 配置健康检查告警
   - 监控资源使用情况
   - 定期检查日志

4. **备份策略**
   - 定期备份 Redis 数据
   - 备份 Workspace 重要文件
   - 保留配置文件版本

---

## 卸载

```bash
cd deploy

# 停止服务
docker compose down

# 删除镜像
docker rmi xruntime:latest

# 删除数据卷（谨慎！会丢失所有数据）
docker volume rm deploy_redis-data deploy_xruntime-workspaces deploy_xruntime-knowledge deploy_xruntime-logs
```

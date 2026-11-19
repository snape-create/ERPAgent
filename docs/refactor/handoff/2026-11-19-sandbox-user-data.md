# 交接文档：沙箱 user-data 持久化、安全加固与上传链路

> 日期：2026-11-19  
> 分支：`thomas`  
> 提交：本次 `feat(sandbox)` 提交（以 `git log -1 --oneline` 为准）  
> 状态：**已完成并通过单测、前端构建、Docker 实机 smoke 与镜像重建**

---

## 1. 任务背景

本轮工作基于 `/tmp/handoff-erp-sandbox-2026-11-19.md` 的 P0/P1/P2 沙箱审计，同时按用户决策把沙箱数据模型向 `deerflow2.0-enhanced` 的 `user-data` 布局靠拢。

最终决策：

| 问题 | 决策 |
|---|---|
| Q1 数据模型 | **B**：目录布局 + 宿主机 bind mount + 持久化 |
| Q2 沙箱粒度 | **A**：每用户一个容器 |
| Q3 持久化范围 | **A**：`uploads` + `outputs` 持久化，`workspace` 临时 |
| MinIO | **不引入** |
| 数据根目录 | `backend/.sandbox/` |
| warm pool | 启用 user-data 挂载时自动停用 |
| 依赖文件 | `docker/requirements.sandbox.txt` |

---

## 2. 最终架构

### 2.1 宿主机目录

```text
backend/.sandbox/
└── users/
    └── {user_id}-{hash}/
        └── user-data/
            ├── .meta.json       # user_id + created_at，供 TTL 清理识别
            ├── uploads/          # 用户上传，持久化
            └── outputs/          # 图表/文档等最终产物，持久化
```

- `workspace` 不进宿主机，保持容器内 tmpfs。
- `.sandbox/` 已加入 `.gitignore`。

### 2.2 容器内目录

```text
/mnt/user-data/uploads   <- 宿主机 uploads（rw bind mount）
/mnt/user-data/outputs   <- 宿主机 outputs（rw bind mount）
/mnt/user-data/workspace <- tmpfs（临时，不持久化）
/opt/pydeps              <- exec tmpfs（Python 依赖）
/skills                  <- skills tmpfs
/workspace               <- 兼容旧路径的 tmpfs
```

### 2.3 路径迁移

| 用途 | 新路径 |
|---|---|
| 图表 | `/mnt/user-data/outputs/charts` |
| 文档 | `/mnt/user-data/outputs` |
| bootstrap 源码 | `/mnt/user-data/workspace/.erpflow` |
| 临时脚本/参数 | `/mnt/user-data/workspace` |
| Python 依赖 | `/opt/pydeps` |

---

## 3. 主要改动

### 3.1 P0 安全修复

- **安全容器创建 fail closed**
  - `sandbox_setup.py`
  - 安全创建失败默认抛错，不再静默创建无防护容器。
  - 仅 `allow_insecure_fallback = true` 允许开发回退。
- **网络模式白名单**
  - 只允许 `none` / `bridge`，拒绝 `host`、`container:*`。
  - 默认 `none`。
- **run-scoped 绑定**
  - 新增 `middlewares/runtime_binding.py`。
  - 每次工具调用绑定 user/store/sandbox，`finally` 复位。
  - 绑定用户后不会回退到 `default_user` 沙箱。
- **路径穿越**
  - 新增 `tools/path_safety.py`。
  - download / document / gateway 全部接入 `safe_filename` + `resolve_under`。
- **宿主机本地回退**
  - 默认关闭；仅 `allow_local_fallback=true` 时开启。
  - 只允许读 `DOWNLOAD_DIR` 内文件。
- **noexec 与 pip**
  - 依赖统一放 `/opt/pydeps`（exec tmpfs）。
  - `PIP_TARGET` / `PYTHONPATH` 指向 `/opt/pydeps`。
  - bootstrap、skill 依赖、web_fetch 已同步。

### 3.2 user-data 与生命周期

- 新模块 `backends/user_data.py`：
  - 路径生成、`.meta.json`、挂载映射。
- `sandbox_entry.py`：
  - 用户容器创建时挂载 uploads/outputs。
  - warm pool 在 user-data 挂载模式下停用。
- `sandbox_manager.py`：
  - 健康检查、容器销毁、Mongo 删除移到锁外。
  - 旧缓存容器缺少 user-data 挂载时自动重建。
  - 空闲回收尊重 `active_runs` lease。
- `sandbox_health.py`：
  - Agent run 期间持有/释放 lease。
- `sandbox_reaper.py`：
  - 启动期清理未登记且超过安全年龄的孤儿用户容器。
- `sandbox_cache.py`：
  - `user_id` 唯一索引。

### 3.3 上传链路

新增 `app/gateway/routers/uploads.py`：

```text
POST   /api/uploads
GET    /api/uploads?user_id=
DELETE /api/uploads/{filename}?user_id=
```

能力：

- 写入 `user-data/uploads`
- 文件名安全校验
- 单文件大小限制：`max_upload_mb`
- 单用户总配额：`max_user_upload_mb`
- 同名文件自动加后缀
- 空文件 400，超限 413

前端：

- `InputBar.tsx` 增加附件按钮、上传状态、文件 chips、删除
- `api.ts` 增加 `uploadFiles` / `deleteUpload`
- 发送时自动追加沙箱路径列表，Agent 可直接读取

### 3.4 TTL 清理

新增 `backends/user_data_cleanup.py`：

- 按 `uploads_ttl_days` / `outputs_ttl_days` 清理过期文件。
- 只删文件，再移除空目录。
- 有运行中 `erp-sandbox-<user>-*` 容器的用户跳过。
- Docker 不可用或缺少 `.meta.json` 时跳过，避免误删。
- 已接入 `pool_maintenance.py` 的后台维护循环。

### 3.5 其他 P1/P2 修复

- bootstrap 失败会销毁刚认领的容器，避免孤儿。
- bootstrap Skills copy 去掉 `|| true`，失败不再静默成功。
- `runtimes` 配置不再被忽略。
- Python 包已预装时跳过运行时 pip。
- `custom_opensandbox.py` shell 插值改用 `shlex.quote`。
- `ping()` 改为实际执行 `true`。
- `download_sandbox_file` 只允许当前 run 绑定的容器，不再扫描任意 `erp-sandbox-*`。
- `install_skill` 不再写宿主全局 `backend/skills`，只写当前用户沙箱 + 用户级 Store。
- `web_fetch` / `install_skill`：
  - 默认拒绝私网 / loopback / link-local / 保留地址。
  - 下载大小上限、重定向上限与二次 URL 校验。
  - 压缩包成员路径校验、文件数/单文件/解压总大小限制。
- tmpfs 大小全部配置化：
  - `workspace_size`
  - `skills_size`
  - `pydeps_size`
  - `uploads_size`
  - `outputs_size`

### 3.6 镜像

- 新增 `docker/requirements.sandbox.txt`。
- `docker/Dockerfile.sandbox` 改用：
  ```dockerfile
  COPY requirements.sandbox.txt /tmp/requirements.sandbox.txt
  RUN pip install --no-cache-dir uv \
      && uv pip install --system --no-cache \
          -r /tmp/requirements.sandbox.txt \
      && rm -f /tmp/requirements.sandbox.txt
  ```
- 已重新构建：`erp-agent-sandbox:latest`（Python 3.13.18）。

---

## 4. 配置项

`backend/config/sandbox_config.toml`：

| 配置 | 默认值 | 说明 |
|---|---|---|
| `network_mode` | `none` | 只允许 none/bridge |
| `allow_insecure_fallback` | `false` | 安全创建失败是否允许回退 |
| `user_data_mounts` | `true` | uploads/outputs bind mount |
| `data_root` | `.sandbox` | 相对 `backend/` |
| `user_data_root` | `/mnt/user-data` | 容器内根路径 |
| `work_dir` | `/mnt/user-data/workspace` | tmpfs 工作区 |
| `python_packages_dir` | `/opt/pydeps` | exec 依赖目录 |
| `max_upload_mb` | `25` | 单文件上传上限 |
| `max_user_upload_mb` | `200` | 单用户 uploads 总配额 |
| `uploads_ttl_days` | `30` | uploads 保留天数 |
| `outputs_ttl_days` | `30` | outputs 保留天数 |
| `user_data_cleanup_interval_seconds` | `3600` | 清理间隔 |
| `allow_local_fallback` | `false` | 宿主机本地回退 |
| `allow_private_web` | `false` | 是否允许 web_fetch 访问私网 |
| `max_fetch_mb` | `5` | web_fetch 下载上限 |
| `max_archive_mb` | `20` | Skill 压缩包下载上限 |
| `max_extracted_mb` | `50` | 解压后总大小上限 |
| `max_archive_files` | `500` | 压缩包文件数上限 |

---

## 5. API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/uploads` | 上传文件（form：`user_id` + `files`） |
| GET | `/api/uploads?user_id=` | 列出上传文件 |
| DELETE | `/api/uploads/{filename}?user_id=` | 删除上传文件 |
| GET | `/api/download/{filename}` | 下载生成文件（已做路径 containment） |

> 注意：当前上传接口的 `user_id` 由请求携带，和聊天接口一致。接入正式登录后必须从服务端 session 推导，不能信任客户端传值。

---

## 6. 验证记录

```text
backend ruff            All checks passed
backend pytest          266 passed, 10 skipped, 20 subtests passed
backend compileall      通过
frontend npm run build  通过
docker build --check    无警告
docker build            成功
```

实机 Docker smoke：

- uploads 宿主机 ↔ 容器读写正常
- outputs 回写宿主机正常
- workspace 是 tmpfs
- `/opt/pydeps` 可执行
- `matplotlib / numpy / pandas` 可导入
- `bootstrap_sandbox` 在新路径下成功上传源码与 skills

新增/更新的测试：

- `tests/test_path_safety.py`
- `tests/test_runtime_binding.py`
- `tests/test_user_data_mounts.py`
- `tests/test_uploads_api.py`
- `tests/test_user_data_cleanup.py`
- `tests/test_sandbox_manager.py`
- `tests/test_sandbox_reaper.py`
- `tests/test_web_fetch_security.py`

---

## 7. 运维与回滚

### 重建沙箱镜像

```bash
cd /home/snape/github_repo/ERP-AGENT-open-source
docker build -f docker/Dockerfile.sandbox -t erp-agent-sandbox:latest docker/
```

### 清理 user-data

```bash
rm -rf backend/.sandbox
```

> 这会删除所有用户的 uploads/outputs 持久数据，执行前确认。

### 查看磁盘占用

```bash
du -sh backend/.sandbox
find backend/.sandbox -maxdepth 4 -type d | head
```

### 回滚

回滚代码：

```bash
git revert <commit>
```

回滚后如需清理新目录：

```bash
rm -rf backend/.sandbox
```

---

## 8. 已知遗留与风险

1. **多进程 warm pool 边缘竞态**
   - `user_data_mounts=true` 时 warm pool 已停用，默认运行路径不受影响。
   - 若未来重新启用 warm pool，需要补跨进程 discovery/锁与 claimed 容器重命名。
2. **`sandbox_proxy` 依赖 deepagents 私有 helper**
   - 已复用 `_build_*` / `_parse_*`；升级 deepagents 时需要跑契约测试。
3. **`last_active_at` 未周期写回 Mongo**
   - 当前空闲回收只依赖内存状态；多进程恢复场景可后续补节流持久化。
4. **上传接口鉴权**
   - 当前沿用项目现状，由请求携带 `user_id`；生产必须改为 session 推导。
5. **上传类型校验**
   - 目前只有文件名、大小、配额限制；尚未做 MIME/扩展名白名单或病毒扫描。
6. **镜像依赖版本**
   - `requirements.sandbox.txt` 使用版本下限；需要完全可复现时用 `uv pip compile` 生成锁文件。
7. **无 MinIO**
   - 数据只存在单机 `backend/.sandbox`；多实例部署需要共享存储或对象存储方案。

---

## 9. 快速接手

1. 先读：
   - `docs/ARCHITECTURE.md` 第 6 节
   - `docs/refactor/sandbox-user-data-design-options.md`
   - 本文档
2. 跑验证：
   ```bash
   cd backend
   .venv/bin/python -m ruff check .
   .venv/bin/python -m pytest -q
   cd ../frontend && npm run build
   ```
3. 确认镜像：
   ```bash
   docker images | grep erp-agent-sandbox
   ```
4. 启动后检查：
   ```bash
   docker ps --format '{{.Names}} {{.Status}}' | grep erp-sandbox
   ls -la backend/.sandbox/users
   ```
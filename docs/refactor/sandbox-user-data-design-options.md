# 沙箱 user-data 目录改造：选项、理由与决策表

> 日期：2026-11-19  
> 分支：`thomas`  
> 状态：**决策已锁定（2026-11-19）：Q1=B、Q2=A、Q3=A，workspace 不持久化，不引入 MinIO；warm pool 选择 A（挂载模式下禁用）；数据根目录 `backend/.sandbox/`。**  
> 实施状态：P0-1～P0-6、user-data 挂载、uploads API（含前端）、配额/TTL、P1 lease/锁外 I/O、P2 安全项均已实现并通过单测/实机 smoke；剩余为多进程 warm pool 边缘竞态。  
> 相关文件：`docs/ARCHITECTURE.md`、`docs/refactor/ROADMAP.md`、`/tmp/handoff-erp-sandbox-2026-11-19.md`

---

## 0. 先对齐事实

### 0.1 当前 ERP 沙箱

- 每个 **用户** 一个容器，不是每个 thread。
- 工作区是 `/workspace` 的 **tmpfs**，容器销毁后数据消失。
- 主要目录：
  - `/workspace/charts`
  - `/workspace/output`
  - `/workspace/data`
  - `/workspace/src`（bootstrap 上传的 harness/skills 源码）
  - `/skills`
- 没有宿主机 bind mount，没有 `uploads` 目录。
- 容器安全基线较强：read-only rootfs、cap-drop、seccomp、资源限制。
- 下载工具目前是“从容器读文件 → 复制到 `backend/download` → HTTP 下载”。

### 0.2 deerflow2.0-enhanced 实际做法

实际目录名是 **`user-data`**（连字符），不是 `user_data`：

```text
{base_dir}/threads/{thread_id}/user-data/
├── workspace/    -> 容器内 /mnt/user-data/workspace
├── uploads/      -> 容器内 /mnt/user-data/uploads
└── outputs/      -> 容器内 /mnt/user-data/outputs
```

关键实现：

- `deerflow/config/paths.py`
  - `VIRTUAL_PATH_PREFIX = "/mnt/user-data"`
  - thread_id 正则校验 `^[A-Za-z0-9_\-]+$`
  - 创建目录后 `chmod 0o777`
  - `resolve_virtual_path()` 做 resolve + containment 检查
- `deerflow/community/aio_sandbox/aio_sandbox_provider.py`
  - 三个宿主机目录 bind mount 到容器
  - skills 以只读挂载到 `/mnt/skills`
  - sandbox_id = `sha256(thread_id)[:8]`
  - 容器名确定性生成
  - 跨进程文件锁 + backend discovery
  - warm pool + replicas + idle timeout（默认 600 秒）
- `deerflow/sandbox/tools.py`
  - 只允许 `/mnt/user-data/*`
  - 拒绝 `..`
  - resolve 后再检查是否仍在 workspace/uploads/outputs 内
  - local 模式会把宿主路径 mask 回虚拟路径
- `deerflow/app/gateway/routers/uploads.py`
  - 上传文件直接写入宿主 `sandbox_uploads_dir(thread_id)`
  - 因此容器重建后 uploads 仍在

### 0.3 不能照抄的部分

deerflow 的沙箱安全参数明显是“能力优先”：

- `seccomp=unconfined`
- 没有 read-only rootfs
- 没有 cap-drop
- 没有 memory/CPU/pids limit
- 没有网络隔离

ERP 不应该为了目录结构而降低现有安全基线。

### 0.4 已完成的最严重 P0

- 安全容器创建失败默认 **fail closed**
- 只有 `allow_insecure_fallback = true` 才允许回退
- 网络模式白名单：只允许 `none` / `bridge`
- 默认网络改为 `none`
- 测试：`195 passed, 10 skipped, 20 subtests passed`

---

## 1. 使用方式

每个问题：

- 在 `[ ]` 里打 `x` 表示选择，例如 `- [x] A. ...`
- 如果选择“其他 / 自由填写”，在题目下方的代码块里直接写
- 如果所有选项都不满意，也可以只写自由填写区
- 不确定的问题可以留空，并在最后一节写备注

---

## Q1. “模仿 deerflow”的边界是什么？

**背景：** 只改目录名很便宜；改持久化挂载会动沙箱生命周期、工具路径、上传链路和清理策略。

- [ ] **A. 只统一目录命名（最小改动）**
  - 把 `/workspace/output` 等改成 `user-data/outputs` 之类。
  - 理由：改动小，风险低。
  - 代价：没有持久化，容器销毁后 uploads/outputs 仍然丢，实际收益有限。

- [ ] **B. 目录布局 + 宿主机 bind mount + 持久化（推荐，第一阶段）**
  - 采用 `user-data/{workspace,uploads,outputs}`。
  - uploads/outputs 持久化到宿主机，容器重建后仍存在。
  - 保留 ERP 当前安全基线。
  - 理由：这是“模仿 deerflow 数据模型”的核心，同时不必一次性重写沙箱生命周期。

- [ ] **C. 完整 deerflow 化：每 thread 容器 + bind mount + HTTP sandbox API + warm pool 全量对齐**
  - 连容器粒度、通信方式、warm pool、discovery 都改。
  - 理由：最接近 deerflow。
  - 代价：改动最大，涉及 manager、graph cache、工具、网关、部署模式。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：B。**  
先拿到持久化数据目录的收益，再评估是否需要 C。C 不应和 P0 修复混在一个 PR 里。

---

## Q2. 沙箱粒度按 user 还是 thread？

**背景：** ERP 现在是每用户一个容器；deerflow 是每 thread 一个容器。粒度决定了“同一用户多个会话是否共享工作区”。

- [ ] **A. 保持每用户一个容器（推荐第一阶段）**
  - 理由：与现有 `sandbox_manager`、graph cache、健康检查、预热池一致，改动最小。
  - 代价：同一用户并发会话共享 workspace，可能互相覆盖文件。

- [ ] **B. 改成每 thread 一个容器（deerflow 风格）**
  - 理由：会话隔离最好，uploads/outputs 按会话独立。
  - 代价：容器数量上升；manager、graph cache、预热池、健康检查都要改；用户级缓存的语义要重定义。

- [ ] **C. 混合：每用户一个容器，但 user-data 下按 thread 分子目录**
  - 例如 `users/{user_id}/threads/{thread_id}/{workspace,uploads,outputs}`。
  - 理由：容器数量不膨胀，同时数据按会话隔离。
  - 代价：工具必须能拿到 `thread_id`；同一容器仍能访问该用户其他 thread 的目录，隔离不彻底。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A（第一阶段），后续按需要升级到 C 或 B。**  
如果第一阶段就选 B，建议单独开一个重构任务，不要和路径安全修复混做。

---

## Q3. 哪些数据必须跨容器重建 / 服务重启存活？

**背景：** 持久化越多，越好用，也越需要清理和配额。

- [ ] **A. uploads + outputs 持久化，workspace 临时（推荐）**
  - 理由：用户上传和最终产物最重要；workspace 里的脚本/缓存可以重建，能控制磁盘增长。
  - 代价：长任务中间状态不能跨容器恢复。

- [ ] **B. uploads + workspace + outputs 全部持久化（deerflow 风格）**
  - 理由：最接近 deerflow；适合需要长期迭代文件的任务。
  - 代价：目录会持续膨胀，需要 TTL、配额和清理策略。

- [ ] **C. 全部临时，只显式导出 outputs**
  - 理由：最干净、最安全。
  - 代价：容器重建后用户上传文件丢失，体验差。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充，例如“workspace 持久 7 天”）
```

**推荐：A。**  
如果用户明确需要“跨会话继续改同一个项目”，再升级到 B。

---

## Q4. 安全基线是否坚持 ERP 当前标准？

**背景：** read-only rootfs、cap-drop、seccomp、资源限制、`network none` 都要不要保留？workspace 若需要跑 `.venv`，会遇到 `noexec` 问题。

- [ ] **A. 全部保留；可执行依赖放独立 exec 挂载（推荐）**
  - 保留 read-only rootfs、cap-drop、seccomp、资源限制、`network none`。
  - workspace 可以 `noexec`；Python 依赖放 `/opt/pydeps` 或独立 `/opt/venv`（exec）。
  - 理由：安全基线不降级，同时解决 C 扩展加载问题。

- [ ] **B. 保留大部分，但 workspace 允许 exec**
  - 允许在 workspace 里创建 `.venv` 并执行。
  - 理由：更接近 deerflow 的“workspace 里随便装”体验。
  - 代价：workspace 是可写可执行区域，攻击面比 A 大。

- [ ] **C. 放松到 deerflow 风格（不推荐）**
  - `seccomp=unconfined`、去掉只读、去掉 cap-drop 等。
  - 理由：兼容性最好。
  - 代价：安全基线明显倒退，不建议生产使用。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A。**  
如果确实需要 workspace `.venv`，选 B，并把 workspace 的资源限制和清理策略一起设计。

---

## Q5. 部署形态：宿主直跑还是 Docker-in-Docker？

**背景：** bind mount 的 `host_path` 在 Docker 里跑时会指向宿主机的路径，需要额外映射。

- [ ] **A. 后端在宿主机直跑（推荐当前阶段）**
  - 理由：当前 README/开发方式就是宿主直跑，bind mount 路径直接可用。
  - 代价：以后容器化时要补路径映射。

- [ ] **B. 后端也在 Docker 里（DooD），现在就把 host path 映射做进去**
  - 类似 deerflow 的 `DEER_FLOW_HOST_BASE_DIR`。
  - 理由：一步到位，部署一致。
  - 代价：配置复杂度上升，本地开发也要处理。

- [ ] **C. 两种都支持，通过配置切换**
  - 理由：最灵活。
  - 代价：需要额外配置项和测试矩阵。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A（当前阶段），代码里预留 host path 映射接口，后续可平滑升级到 C。**

---

## Q6. uploads 由谁写入？

**背景：** 当前 ERP 没有对应的上传路由；只建目录没有写入链路，uploads 会是空的。

- [ ] **A. 本阶段新增网关上传接口（推荐）**
  - 例如 `POST /api/threads/{thread_id}/uploads`。
  - 写入宿主机 `user-data/uploads`。
  - 理由：让 uploads 真正可用；与 deerflow 行为一致。
  - 必须同时做：大小限制、文件名安全、MIME/类型限制、鉴权。

- [ ] **B. 先只建目录，上传链路后续再做**
  - 理由：缩小本次改造范围。
  - 代价：uploads 暂时没有实际数据来源。

- [ ] **C. 通过 Agent 工具上传，不走网关**
  - 理由：实现集中。
  - 代价：用户上传体验差，且模型可控文件写入风险更高。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A。**  
如果本次只想改沙箱层，选 B，但要在文档里明确 uploads 尚未接通。

---

## Q7. 产物下载怎么处理？

**背景：** 现在是从容器复制到 `backend/download` 再提供 HTTP；如果 outputs 已经持久化到宿主机，可以直接提供。

- [ ] **A. 继续复制到 `backend/download`（推荐当前阶段）**
  - 理由：复用现有 `/api/download`、现有下载工具和路径安全逻辑；URL 稳定。
  - 代价：多一次复制，磁盘有重复文件。

- [ ] **B. 直接从宿主机 `outputs` 提供下载**
  - 理由：少一次复制，文件天然持久。
  - 代价：必须做用户隔离、路径 containment、鉴权和文件名处理。

- [ ] **C. 两者都保留**
  - 理由：兼容旧链接，逐步迁移。
  - 代价：需要定义优先级和清理规则。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A。**  
等 B 的鉴权和路径校验补齐后，再考虑切换。

---

## Q8. 清理策略是什么？

**背景：** `user-data` 是持久目录，不清理会无限增长。

- [ ] **A. 按 TTL 清理 + 配额（推荐）**
  - uploads / workspace / outputs 分别设置保留时间。
  - 例如：workspace 7 天，uploads 30 天，outputs 30 天（只是示例）。
  - 理由：可控、可运维。
  - 代价：需要后台清理任务和配置项。

- [ ] **B. 跟随沙箱销毁一起删除**
  - 理由：实现简单。
  - 代价：容器重建就丢数据，和“持久化”目标冲突。

- [ ] **C. 永久保留，只靠人工清理**
  - 理由：不丢数据。
  - 代价：磁盘会持续增长，生产不可接受。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充，例如具体天数、单用户配额）
```

**推荐：A。**  
建议把清理策略和目录设计放在同一个任务里，避免后面补丁式修改。

---

## Q9. 路径命名统一成什么？

**背景：** deerflow 用 `/mnt/user-data`；ERP 现在用 `/workspace`。工具、提示词、bootstrap、skills 都引用了旧路径。

- [ ] **A. 严格对齐 deerflow：`/mnt/user-data/{workspace,uploads,outputs}`（推荐）**
  - 代码内用常量，不硬编码。
  - 理由：语义清晰；与参考实现一致；uploads/outputs 的职责明确。
  - 代价：需要迁移 `/workspace/charts`、`/workspace/output`、`/workspace/src` 等引用。

- [ ] **B. 保留 `/workspace`，增加 `user-data` 作为别名/符号链接**
  - 理由：兼容旧工具和提示词。
  - 代价：路径语义混乱，长期维护成本高。

- [ ] **C. 路径前缀可配置**
  - 理由：最灵活。
  - 代价：测试和文档要覆盖多种配置。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A，同时把所有路径集中到 config 常量。**  
不要在多处散落硬编码字符串。

---

## Q10. 依赖策略怎么分？

**背景：** 镜像依赖、Skill 依赖、workspace `.venv` 是三件不同的事。

- [ ] **A. 镜像依赖用 `docker/requirements.sandbox.txt` 预装；运行时依赖装到独立 exec 挂载（推荐）**
  - 镜像依赖：matplotlib、numpy、pandas、字体等。
  - 运行时 Skill 依赖：装到 `/opt/pydeps` 或独立 venv。
  - 理由：镜像可复现；运行时不需要联网；与 `network none` 兼容。
  - 代价：运行时动态装包能力有限。

- [ ] **B. 镜像依赖用 pyproject + uv.lock**
  - 理由：锁定更严格，适合依赖复杂的场景。
  - 代价：需要独立 `docker/sandbox/pyproject.toml`，不要混进 backend pyproject。

- [ ] **C. 允许运行时在 workspace `.venv` 里 pip install**
  - 理由：最灵活，接近 deerflow。
  - 代价：需要网络（不能默认 `none`），workspace 要 exec，安全和清理成本上升。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

**推荐：A。**  
如果以后需要严格锁定，再升级到 B；如果必须动态装包，选 C 并同时调整网络策略。

---

## 2. 依赖文件：requirements.txt vs pyproject.toml

### 2.1 推荐方案

- [ ] **A. `docker/requirements.sandbox.txt`（推荐）**
  - 构建：
    ```dockerfile
    COPY requirements.sandbox.txt /tmp/requirements.sandbox.txt
    RUN uv pip install --system --no-cache -r /tmp/requirements.sandbox.txt
    ```
  - 如果需要锁定：
    ```bash
    uv pip compile docker/requirements.sandbox.in \
      -o docker/requirements.sandbox.txt
    ```
  - 理由：
    - 沙箱依赖是运行时契约，不是 Python 项目打包。
    - 与 backend/harness 的 `pyproject.toml` 解耦。
    - diff 直观，审计简单。
    - 与用户 Skill 的 `requirements.txt` 心智一致。
  - 代价：需要自己维护锁定文件；没有 extras/groups 机制。

- [ ] **B. 独立 `docker/sandbox/pyproject.toml` + `uv.lock`**
  - 构建：
    ```dockerfile
    RUN uv sync --frozen --no-install-project
    ```
  - 理由：严格锁定、支持 extras/groups、适合复杂依赖。
  - 代价：多一个项目文件；需要处理安装到 system 还是 venv；和 backend pyproject 容易混淆。

- [ ] **C. 混合：pyproject 作为源，导出 requirements 给镜像**
  - 例：
    ```bash
    uv export --frozen --no-dev --format requirements-txt \
      > docker/requirements.sandbox.txt
    ```
  - 理由：一处声明，两种消费者。
  - 代价：多一步生成流程，团队要记住同步。

- [ ] **D. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

### 2.2 明确不推荐

- 不要把沙箱依赖直接加进：
  - `backend/pyproject.toml`
  - `backend/packages/harness/pyproject.toml`
- 理由：会把应用依赖和沙箱运行时依赖绑死，镜像构建变慢，依赖审计混乱。

### 2.3 与网络模式的关系

如果默认 `network_mode = "none"`：

- 镜像依赖必须预装；
- 运行时 `pip install` 默认会失败；
- Skill 依赖若需要运行时安装，必须：
  - 改用内部包代理；
  - 或临时切换到 `bridge`；
  - 或改成离线 wheel 预置。

---

## 3. 我的最终选择汇总

请把每题的选项字母填到右边，或直接写“按推荐”。

| 问题 | 你的选择 | 备注 |
|---|---|---|
| Q1. 模仿边界 | **B** | 目录布局 + 宿主机 bind mount + 持久化 |
| Q2. 沙箱粒度 | **A** | 每用户一个容器 |
| Q3. 持久化范围 | **A** | uploads + outputs 持久化，workspace 临时；不引入 MinIO |
| Q4. 安全基线 | **A** | 保留 read-only / cap-drop / seccomp / 资源限制 / `network none`；可执行依赖放独立 exec 挂载 |
| Q5. 部署形态 | **A** | 后端宿主机直跑；预留 host path 映射接口 |
| Q6. uploads 写入 | **A** | 新增网关上传接口 |
| Q7. 产物下载 | **A** | 继续复制到 `backend/download` |
| Q8. 清理策略 | **A** | TTL 清理 + 配额 |
| Q9. 路径命名 | **A** | `/mnt/user-data/{workspace,uploads,outputs}` |
| Q10. 依赖策略 | **A** | 镜像依赖预装；运行时依赖装到独立 exec 挂载 |
| 依赖文件 | **A** | `docker/requirements.sandbox.txt` |

---

## 3.1 最终方案摘要

- **每个用户一个容器**，不按 thread 创建容器。
- 宿主机数据根目录（名称待定，建议 `backend/sandbox_data/` 或 `backend/.sandbox/`）：

```text
{data_root}/users/{user_id}/user-data/
├── uploads/
└── outputs/
```

- 容器内挂载：

```text
/mnt/user-data/uploads  <- 宿主机 uploads（rw bind mount）
/mnt/user-data/outputs  <- 宿主机 outputs（rw bind mount）
/mnt/user-data/workspace <- tmpfs（临时，不持久化）
```

- **不引入 MinIO。**
- `uploads` / `outputs` 在容器销毁、服务重启后仍然存在。
- `workspace` 在容器重建后丢失。
- 同一用户的所有会话共享同一套 `uploads` / `outputs`。

## 3.2 关键冲突：预热池无法挂载 per-user 目录

当前预热池是在用户认领之前创建通用容器。Docker 的 bind mount 在容器创建时固定，**认领后无法再改成该用户的 uploads/outputs 目录**。

因此在 Q1=B + Q2=A 下，必须二选一（或改造）：

- [x] **A. 启用 user-data 挂载时禁用 warm pool，按用户直接创建容器（已确认）**
  - 理由：实现正确，目录语义清晰。
  - 代价：首次请求冷启动变慢，`warm_pool_size` 在该模式下失效。

- [ ] **B. 保留 warm pool，改为容器创建后同步文件**
  - 理由：保留预热性能。
  - 代价：不再是 bind mount，违背 Q1=B；需要同步上传/回写产物，复杂度和丢失风险都更高。

- [ ] **C. 其他 / 自由填写**

```text
（在这里写你的选择或补充）
```

## 3.3 多进程共享同一 user-data 的风险

同一用户的多个 LangGraph 进程可能各自创建一个容器，并挂载同一个 user-data 目录，造成并发写冲突。

需要至少选一个方向：

- 单进程写 + 文件锁/租约；
- 或禁止多进程各自创建同用户容器；
- 或后续用 P1 的 lease / 跨进程 discovery 解决。

当前建议：先明确部署是单进程还是多进程，再决定是否在本次改造里补租约。

---

## 3.4 workspace 里具体是什么

当前 `/workspace` 不是单一用途目录，里面混了几类东西：

| 当前路径 | 内容 | 生产者 | 是否应该持久化 |
|---|---|---|---|
| `/workspace/src` | bootstrap 上传的 `erpflow` 源码 + `backend/skills` | `agent/bootstrap.py` | ❌ 临时/可重建 |
| `/workspace/.erp-bootstrap-fingerprint` | 项目上传指纹 | `agent/bootstrap.py` | ❌ 临时 |
| `/workspace/.erp-agent-project.tar.gz` | 上传压缩包临时文件 | `agent/bootstrap.py` | ❌ 临时 |
| `/workspace/python-packages` | pip 依赖目标（当前实现，P0-6 待修） | 运行时 pip | ❌ 应移到 `/opt/pydeps` |
| `/workspace/requirements_<skill>.txt` | Skill 依赖临时文件 | `web_fetch.py` | ❌ 临时 |
| `/workspace/charts` | 图表 PNG + 临时 `_params_*.json` / `_chart_script_*.py` | `chart_generator.py` | ⚠️ 图表本身应移到 `outputs`，脚本/参数临时 |
| `/workspace/output` | Markdown/HTML/CSV/JSON/TXT 报告 | `document_generator.py` | ⚠️ 应移到 `outputs` |
| `/workspace/data` | 预建目录，供 agent/数据文件使用 | bootstrap / agent | ⚠️ 取决于是否当作用户数据 |
| `/workspace/analysis` | 预建目录，供分析中间产物使用 | sandbox_entry | ⚠️ 中间产物 |
| `/workspace` 下任意文件 | agent 通过文件工具/bash 创建的脚本、notebook、CSV、解压文件等 | agent | ⚠️ 纯 scratch |

### 推荐归类

**1. 不应该进 user-data 的东西：**

- bootstrap 源码与指纹
- 上传压缩包
- pip 依赖
- Skill requirements 临时文件
- 图表临时脚本/参数

这些应该放：

```text
/mnt/user-data/workspace/.erpflow/   # bootstrap 临时，tmpfs
/opt/pydeps/                         # Python 依赖，exec tmpfs/镜像
```

**2. 应该进 outputs 的东西：**

- 图表 PNG
- Markdown/HTML/CSV/JSON/TXT 报告
- 用户可下载的最终产物

**3. 纯 workspace scratch：**

- agent 写的临时脚本
- 中间 CSV/JSON
- 解压文件
- notebook
- 调试输出
- `data/`、`analysis/` 下的中间结果

### 对 Q3 的具体含义

如果 Q3 = A（当前选择）：

```text
/mnt/user-data/workspace -> tmpfs，容器重建后清空
```

会丢：临时脚本、中间数据、`.erpflow` 源码、调试文件。  
不会丢：`uploads`、`outputs`（只要图表/文档工具改成写 `outputs`）。

如果改成 Q3 = B：

```text
/mnt/user-data/workspace -> 宿主机 bind mount，持久化
```

会保留：所有 agent scratch、中间文件、脚本、notebook。  
代价：目录持续增长；脚本可能被保留；需要 TTL/配额；如果 workspace 允许执行，还要重新评估 Q4。

### 待确认

- [x] **A. workspace 不持久化（已确认，保持 Q3=A）**
  - 图表和文档改写到 `/mnt/user-data/outputs`。
  - workspace 只放临时文件。

- [ ] **B. workspace 持久化（Q3=B）**
  - 保留所有 agent scratch。
  - 需要额外 TTL/配额和清理策略。

- [ ] **C. 只持久化 workspace 下的部分子目录**
  - 例如只持久化 `data/`、`analysis/`，不持久化临时脚本和 `.erpflow`。
  - 需要更细的目录约定。

```text
（在这里写你的选择或补充）
```

---

## 4. 自由补充区

```text
（这里可以自由写任何约束、偏好、部署要求、时间安排、目录示例等）





```

---

## 5. 下一步

已确认：

- 3.2 选 A：启用 user-data 挂载时禁用 warm pool。
- 数据根目录：`backend/.sandbox/`。
- 3.4 选 A：workspace 不持久化。

建议顺序：

1. 先完成剩余 P0：run-scoped 绑定、路径穿越、宿主机任意文件读取、exec 依赖位置。
2. 实现 `backend/.sandbox/users/{user_id}/user-data/{uploads,outputs}` 与 bind mount。
3. 迁移工具路径、bootstrap、skills、uploads。
4. 最后补清理任务、配额、下载链路和测试。
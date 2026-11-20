# 沙箱 user-data 改造复核报告

> 日期：2026-11-19  
> 复核对象：`afa15cd feat(sandbox): 完成 user-data 持久化、上传链路与沙箱安全加固`  
> 分支：`thomas`  
> 复核方式：交接文档 + commit diff + 当前代码 + 单测 + Docker 实机 smoke  
> 本轮只做复核，未修改业务代码。

---

## 1. 复核范围

- 交接文档：`docs/refactor/handoff/2026-11-19-sandbox-user-data.md`
- 设计选项：`docs/refactor/sandbox-user-data-design-options.md`
- 提交：`afa15cd`
- 沙箱生命周期、user-data、上传、下载、Skill、web_fetch、runtime binding 相关模块
- 后端测试、ruff、Docker 实机 smoke

验证结果：

```text
backend ruff            All checks passed
backend pytest          266 passed, 10 skipped, 20 subtests passed
```

---

## 2. 总体结论

这次改造把上一轮审计里的很多 P0 问题真正修掉了，沙箱从“基本不可用”提升到了**单机、可信用户场景基本可运行**。

但还不能认为已经完成，尤其是以下问题会影响多用户/生产环境：

- lease 不是 per-run 的，并可能永久泄漏
- 长期运行的 stale 容器会永久阻止 user-data TTL 清理
- seccomp 配置缺失/损坏时仍然 fail-open
- `web_fetch` 重定向 SSRF 仍然存在
- 上传配额可通过 `.part` 文件名绕过
- `/api/download` 仍是全局共享目录，没有用户隔离
- Mongo 不可用时可能创建重复容器
- 多个 tmpfs 配置实际没有生效

---

## 3. 已确认修复的部分

| 原问题 | 当前状态 |
|---|---|
| 安全创建失败静默退化为无防护容器 | ✅ 已修：默认 fail closed，`allow_insecure_fallback` 显式开启 |
| 默认网络为 bridge | ✅ 已修：默认 `none`，白名单只允许 `none` / `bridge` |
| 工具无用户级 sandbox/store 绑定 | ✅ 已修：`RuntimeBindingMiddleware` 在 tool call 外层绑定并在 `finally` 复位 |
| download/document 文件名穿越 | ✅ 已修：`path_safety.safe_filename` / `resolve_under`，gateway 下载路由也接入 |
| download 本地回退可读宿主任意文件 | ✅ 已修：默认关闭，开启后限制在 `DOWNLOAD_DIR` |
| noexec 与 pip target 冲突 | ✅ 已修：依赖改到 `/opt/pydeps`，可执行 tmpfs；实测 numpy/matplotlib 可导入 |
| user-data 挂载 | ✅ 已实现：实测 uploads/outputs 双向读写正常 |
| warm pool 与 user-data 冲突 | ✅ 已处理：启用 user-data 时 warm pool 停用 |
| Skill 写宿主全局目录 | ✅ 已修：改为当前用户沙箱 + 用户级 Store |
| bootstrap Skills 复制失败被 `\|\| true` 吞掉 | ✅ 已修 |
| shell 路径插值 | ✅ 大部分已改 `shlex.quote` |
| `ping()` 只看容器状态 | ✅ 已修：实际执行 `true` |
| sandbox_cache user_id 唯一索引 | ⚠️ 已实现，但失败后不会重试 |
| TTL 清理 | ⚠️ 已实现，但与长期运行缓存容器冲突 |
| 上传接口 | ⚠️ 已实现，但仍有配额绕过和鉴权问题 |

---

## 4. P0 / P1：仍然需要处理的问题

### 4.1 Lease 不是 per-run 的，并可能永久泄漏

相关文件：

- `backend/packages/harness/erpflow/middlewares/sandbox_health.py:47,71-103`
- `backend/packages/harness/erpflow/backends/sandbox_manager.py:395-426,517-533`

当前 `SandboxHealthMiddleware` 用的是一个布尔值：

```python
self._lease_held = False
```

同一用户的 Agent graph 会被缓存复用，可能出现两个并发 run：

1. Run A `before_agent` → `acquire_lease`，`active_runs=1`
2. Run B `before_agent` → 看到 `_lease_held=True`，不再 acquire
3. Run A `after_agent` → release，`active_runs=0`
4. Run B 还在执行，但空闲清理已经认为没有活跃 run

实测 fake manager：

```text
after two concurrent before_agent acquisitions: 1
after one run release: 0
```

另外，`after_agent` 在以下场景不一定会执行：

- HITL interrupt
- 客户端断流
- 图执行异常/取消

如果它不执行，lease 会一直保持，空闲回收永远跳过该容器；如果 `cleanup_expired_user_data` 又跳过“有运行中容器”的用户，文件 TTL 也会被永久阻塞。

建议：

- lease 改成 per-run token / 计数，不要在 middleware 实例上只放 bool
- `before_agent` 每次 run 都 acquire
- 在 stream 结束、异常、interrupt、取消路径都保证 release
- `rebuild` 和 `health_check_all` 检查 `active_runs`
- 为“中断后不再 resume”的会话加超时回收策略

---

### 4.2 TTL 清理会被 stale 容器永久阻塞

相关文件：

- `backend/packages/harness/erpflow/backends/user_data_cleanup.py:114-117`
- `backend/packages/harness/erpflow/backends/sandbox_reaper.py:61-94`
- `backend/packages/harness/erpflow/backends/sandbox_manager.py:465-500`

`cleanup_expired_user_data()` 只要发现该用户有运行中的容器，就整户跳过：

```python
if any(name.startswith(prefix) for name in active_container_names):
    continue
```

问题链路：

1. 进程重启后，manager 的内存 `_entries` 是空的。
2. 上一次进程留下的 user 容器可能还在运行，并且 MongoDB cache 里还有记录。
3. 新进程不会把它放进 `_entries`，所以 `cleanup_idle_sandboxes()` 看不到它。
4. `cleanup_stale_user_containers()` 只在启动时清理“缓存未登记”的容器。
5. 这个容器在缓存里有登记，因此被保护。
6. 但它仍在 Docker 中运行，所以 TTL 清理一直跳过该用户。

结果：**只要缓存里还有一个长期运行的 user 容器，该用户的 uploads/outputs TTL 就永远不会生效。**

此外，`last_active_at` 只在 claim/create 时写入 Mongo，`touch()` 只改内存，不能用于跨进程判断最近是否活跃。

建议：

- 区分“容器 running”和“该用户最近有活跃 run”
- TTL 清理不要只看 Docker running，应结合 lease / 最近访问时间
- 增加启动后定期清理 cached idle user container 的机制
- 给容器打上 user_id / last_used / managed-by label，而不是只靠名称

---

### 4.3 seccomp 配置缺失或损坏时仍然 fail-open

相关文件：

- `backend/packages/harness/erpflow/backends/sandbox_setup.py:121-132,217-225`

当前逻辑：

- `use_seccomp=true`
- 如果 seccomp 文件不存在：只 warning，然后继续创建容器
- 如果 JSON 无效：只 warning，然后不添加 seccomp

也就是说，即使配置写着启用 seccomp，容器也可能在**没有 seccomp** 的情况下启动。

建议：

- `use_seccomp=true` 时，文件缺失/解析失败必须直接报错
- 如果是显式关闭 seccomp，才允许继续
- 把 seccomp 是否实际生效写入启动日志或容器 label，方便审计

---

### 4.4 web_fetch 重定向 SSRF 仍然存在

相关文件：

- `backend/packages/harness/erpflow/tools/web_fetch.py:107-140`

当前使用：

```python
httpx.Client(
    follow_redirects=True,
    max_redirects=5,
    ...
)
```

然后：

```python
final_url = str(response.url)
_validate_url(final_url)
```

问题在于：**请求已经发出去了，才校验最终 URL。** 如果一个公网 URL 302 到 `http://127.0.0.1` 或内网地址，httpx 会先向内部地址发起请求，然后代码才发现并拒绝。对于有副作用的内部 GET 接口，这就是实际 SSRF。

建议：

- 关闭自动重定向
- 手动循环处理 3xx
- 在每次 `Location` 解析并校验之后，再发起下一次请求
- 对 DNS rebinding、代理环境变量也做明确策略

---

### 4.5 上传接口：配额可绕过，且仍信任客户端 user_id

相关文件：

- `backend/app/gateway/routers/uploads.py:48-54,57-90,93-119`

#### `.part` 配额绕过

`_current_usage()` 明确忽略以 `.part` 结尾的文件：

```python
if p.is_file() and not p.name.endswith(".part")
```

但 `safe_filename()` 允许用户上传名为 `foo.part` 的文件，最终文件就是 `foo.part`，且 `list_uploads()` 也把它隐藏掉。

用户可以反复上传 `.part` 文件，绕过单用户配额，并让目录占用无限增长。

#### 并发配额竞态

多个并发上传请求会各自读取 `_current_usage()`，然后在 `_save_upload()` 中累加。没有锁或配额预留，两个请求可能同时通过配额检查，最终超过上限。

#### user_id 鉴权

当前 `user_id` 来自表单，和聊天接口一样信任客户端。交接文档已经明确标注了这一点，但如果目标是多用户生产环境，这仍然是 P0：任何调用方都可以读写其他用户的 uploads。

建议：

- 拒绝用户上传名以 `.part` 结尾，或使用服务端专用临时前缀
- 配额检查和写入需要锁/原子预留
- 生产环境从登录 session 推导 user_id
- 不在 API 响应中返回宿主绝对路径

---

### 4.6 `/api/download` 是全局共享目录，没有用户隔离

相关文件：

- `backend/app/gateway/app.py:67-98`
- `backend/packages/harness/erpflow/tools/download_sandbox_file.py`
- `backend/packages/harness/erpflow/tools/document_generator.py`

`download_sandbox_file` 和 `generate_document` 最终都写入全局 `DOWNLOAD_DIR`，而下载路由没有用户维度：

```python
@app.get("/api/download/{filename}")
async def download_file(filename: str):
    file_path = resolve_under(DOWNLOAD_DIR, filename)
```

结果是：

- 用户 A 可以按文件名读取用户 B 导出的文件
- 用户 A 可以用相同文件名覆盖用户 B 的导出文件
- 路径安全只保证不出 `DOWNLOAD_DIR`，不保证文件属于当前用户

图表文件名有随机 uuid，风险稍低；文档文件名可能更可预测；如果用户知道文件名，就可以跨用户访问。

建议：

- 按用户拆 `DOWNLOAD_DIR/<user_id>/`
- 或下载时返回不可猜测 token，并在服务端记录 token → 文件 + user
- 下载路由校验当前请求用户是否拥有该文件

---

## 5. P1：配置、缓存和生命周期问题

### 5.1 多个 tmpfs 配置实际上没有生效

相关文件：

- `backend/packages/harness/erpflow/backends/sandbox_setup.py:51-55,155-167`
- `backend/packages/harness/erpflow/config/sandbox.py:138-142`

`SandboxConfig` 里有：

- `workspace_size`
- `skills_size`
- `pydeps_size`

但 `create_secure_sandbox()` 的 tmpfs 实际写死了：

```python
"/workspace": "rw,noexec,nosuid,size=256m",
f"{SANDBOX_USER_DATA_ROOT}/workspace": "rw,noexec,nosuid,size=256m",
SANDBOX_PYTHON_PACKAGES_DIR: "rw,exec,nosuid,nodev,size=512m",
"/skills": "rw,nosuid,nodev,size=128m",
```

只有：

- `tmpfs_size`
- `uploads_size`
- `outputs_size`

在部分路径上被使用。

`workspace_size`、`skills_size`、`pydeps_size` 目前是**死配置**。交接文档说“tmpfs 大小全部配置化”，这一点不成立。

另外：

- `/workspace` 和 `SANDBOX_WORK_DIR` 的路径没有绑定
- `/skills` 和 `SANDBOX_SKILLS_DIR` 的路径没有绑定

如果配置改了 `work_dir` 或 `skills_dir`，container 的 tmpfs 仍然挂旧路径，bootstrap 却写新路径，可能出现只读 rootfs 写入失败。

建议统一使用：

```python
config.workspace_size
config.skills_size
config.pydeps_size
SANDBOX_WORK_DIR
SANDBOX_SKILLS_DIR
```

---

### 5.2 Mongo 不可用时可能创建重复容器

相关文件：

- `backend/packages/harness/erpflow/backends/sandbox_cache.py:74-84`
- `backend/packages/harness/erpflow/backends/sandbox_manager.py:305-360`

`SandboxCache.find()` 遇到 Mongo 异常时返回 `None`，调用方无法区分：

- 真的没有缓存
- Mongo 暂时不可用

于是 `get_sandbox()` 会走新建容器路径。如果此时旧容器还在运行，但新进程读不到缓存，就会创建**第二个容器**，两个容器同时 bind 同一个用户 user-data 目录，产生并发写风险。

实机 smoke 时 Mongo 不可用，实际观察到：

- `get_sandbox()` 的缓存读取和写入各等待约 10 秒
- 最终仍创建了容器
- `create_index` 失败
- 缓存未写入

建议：

- Mongo 读失败时不要静默当作“无缓存”
- 要么 fail closed，要么使用可恢复的本地/内存锁
- 缩短 Mongo 失败路径的超时
- 缓存索引创建失败后不要永久标记 `_index_ready=True`

---

### 5.3 缓存索引失败后不会重试

`sandbox_cache.py:35-47`：

```python
except Exception as exc:
    sandbox_logger.warning(...)
self._index_ready = True
```

即使 `create_index` 失败，也会把 `_index_ready` 设为 True。之后即使 Mongo 恢复，也不会再尝试创建唯一索引，`user_id` 重复文档风险重新出现。

建议：只有创建成功才设置 `_index_ready=True`，失败时下轮重试。

---

### 5.4 生命周期回收与缓存删除存在竞态

`cleanup_idle_sandboxes()` 和 `destroy_user_sandbox()` 会在锁外删除 Mongo 缓存。

可能发生：

1. idle cleanup 选出旧 entry，移除内存状态
2. 另一个请求创建了同一用户的新容器并写入新缓存
3. idle cleanup 执行 `_cache.delete(user_id)`
4. 新容器的缓存被误删
5. 下次重启，新容器变成未保护容器，可能被 reaper 清掉

建议：

- 删除缓存前确认 `_user_map[user_id]` 仍指向旧容器
- 或把 cache 更新和内存状态变更放到同一个带版本的协调流程里

---

### 5.5 `/state` 仍可能触发图构建和沙箱认领

相关文件：

- `backend/app/gateway/routers/chat.py:70-82`
- `backend/packages/harness/erpflow/graph.py:23-27`

`threads.get_state()` 由 LangGraph Server 处理，可能触发图工厂。图工厂仍有：

```python
user_id=configurable.get("user_id", "default_user")
```

如果 state 读取没有带用户 config，就会落到 `default_user`，并可能触发 `get_or_build()` → `get_sandbox()`。

这是之前 `docs/refactor/reports/W1a-event-mapping.md` 里记录过的 R10，当前代码没有看到明确修复。

建议：

- 图工厂识别非执行上下文，不获取沙箱
- 或 state 读取路径不构建 Agent 图
- 至少不要在 state 查询时认领/重建沙箱

---

### 5.6 Skill 依赖安装与 `network_mode=none` 冲突

`web_fetch.install_skill()` 会在沙箱里执行：

```text
pip install -r requirements.txt --target /opt/pydeps
```

但默认容器网络是 `none`。如果镜像没有预装该 Skill 的全部依赖，pip 安装会失败；当前只记录 warning，安装流程仍然返回“Skill 安装成功”。

结果可能是：

- Skill 文件存在
- 但依赖缺失
- Agent 使用 Skill 时才失败

建议：

- 明确区分“文件安装成功”和“依赖安装成功”
- 依赖安装失败时返回失败或警告状态
- 或者提供安装期临时网络和显式 allowlist
- 或者要求 Skill 依赖预先打入镜像

---

## 6. P2：值得后续清理的问题

### 6.1 下载大文件没有上限

`download_sandbox_file` 直接：

```python
read_result = container.exec_run(["base64", remote_path], demux=True)
content = base64.b64decode(stdout.strip())
```

没有输出大小限制。若用户/模型请求一个数 GB 的沙箱文件，会一次性读入宿主内存。

建议使用现有 `download_files` 的分块/限制逻辑，或加最大下载大小。

### 6.2 压缩包解压的实际大小仍可能绕过限制

`web_fetch._extract_archive_to_memory()` 主要依赖 ZIP/TAR 声明的大小：

- `info.file_size`
- `member.size`

恶意压缩包可以伪造声明大小，实际解压时仍可能产生巨大内容。建议使用带硬上限的流式读取，读取过程中累计并截断。

### 6.3 HTML 输出有 inline XSS 风险

`document_generator` 会把模型内容直接写入 HTML，而 gateway 对 `text/html` 使用 `inline` 展示：

```python
inline = media_type.startswith("text/") ...
```

恶意生成内容可能执行脚本。建议：

- HTML 内容做转义/净化，或
- 强制 `attachment` 下载，或
- 使用独立的文本预览服务

### 6.4 上传接口返回宿主路径

`uploads.py:119`：

```python
return {"user_id": user_id, "files": saved, "uploads_dir": str(base)}
```

`uploads_dir` 是宿主绝对路径，会暴露后端目录结构。前端目前不使用，建议删除。

### 6.5 本地绝对路径仍会出现在工具输出

`document_generator` 本地回退和 `download_sandbox_file` 都会把 `本地路径` 放进工具返回文本。模型可能将其转述给用户，造成信息泄露。建议只返回下载 URL 和逻辑文件名。

### 6.6 剩余 shell 插值

`CustomOpenSandbox.pip_install()` 仍然是：

```python
f"pip install {package} -q"
```

`web_fetch._validate_skill_in_sandbox()` 对 `py_file` / `sandbox_skill_dir` 仍使用单引号拼接。虽然主要影响沙箱内部，但建议统一 `shlex.quote` 或列表参数。

### 6.7 多实例部署假设

当前 user-data 在单机 `backend/.sandbox`：

- 没有 MinIO
- 没有共享存储
- gateway 和 LangGraph Server 必须看到同一个宿主路径
- 多实例部署下数据不会自动共享

这是设计上已知的限制，但需要在部署文档里写清楚。

---

## 7. 实机 smoke 记录

实际创建并销毁了一个 `__report_smoke__` 用户沙箱，确认：

- `ReadonlyRootfs=True`
- `NetworkMode=none`
- `Memory=512m`
- `NanoCpus=1`
- `PidsLimit=256`
- `CapDrop=["ALL"]`
- `CapAdd=["CHOWN","SETUID","SETGID","DAC_OVERRIDE"]`
- `/opt/pydeps` 是 `rw,exec`
- `/workspace`、`/mnt/user-data/workspace`、`/tmp` 是 `noexec`
- uploads/outputs bind mount 正确
- uploads 宿主机 → 容器读取正常
- 容器写 outputs → 宿主机可见
- `matplotlib/numpy/pandas` import 正常

同时观察到 Mongo 不可用时：

- cache 读取/写入各约 10 秒
- `create_index` 失败
- 仍然创建了容器
- 缓存没有写入

测试容器和测试 user-data 目录已经清理，仓库保持干净。

---

## 8. 建议修复顺序

**第一批，必须处理：**

1. Lease per-run 化和 interrupt/异常释放
2. TTL 不再被 stale running container 永久阻塞
3. seccomp 缺失/损坏时 fail closed
4. web_fetch 手动重定向校验，修 SSRF
5. 下载目录按用户隔离，下载路由做 ownership 校验
6. 上传 `.part` 配额绕过 + 并发配额 + user_id 鉴权

**第二批，稳定性和资源：**

7. tmpfs size / work_dir / skills_dir 配置真正接入
8. Mongo 不可用不要静默新建重复容器
9. cache 索引失败重试
10. 修正 idle cleanup / destroy 的缓存删除竞态
11. `/state` 非执行路径不认领沙箱
12. Skill 依赖安装状态明确化

**第三批，安全与体验：**

13. 下载大小上限、压缩包流式解压上限
14. HTML 输出转义或强制下载
15. 不再返回宿主路径
16. 剩余 shell 插值统一处理
17. 多实例共享存储方案

---

## 9. 总体评价

这次提交的质量比上一版明显提升，尤其是：

- 安全容器 fail closed
- user-data bind mount
- runtime binding
- path safety
- `/opt/pydeps` 可执行依赖目录
- Skills 不再污染宿主全局目录

但目前还存在几个会影响**多用户生产运行**的关键问题，主要是 lease 语义、stale 容器与 TTL、seccomp fail-open、上传鉴权/配额和跨用户下载目录隔离。

建议在没有修完 P0 之前，把它定位为“单机可信环境可用”，不要直接开放给多租户生产使用。
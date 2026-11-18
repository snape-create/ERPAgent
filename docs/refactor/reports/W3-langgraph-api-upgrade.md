# W3 — LangGraph Server 0.7.103 → 0.18.x 升级评估（只读调查）

> 调查方式：读取 `backend/.venv` 中已安装的 `langgraph-api 0.7.103` 源码；从腾讯镜像下载
> `langgraph_api-0.18.3`、`langgraph_runtime_inmem-0.38.3` wheel 到 `/tmp` 后逐文件对比；
> 依赖可解性用 `uv pip compile` 在 `/tmp` 的临时 requirements 上验证，未触碰仓库依赖。
> 除本报告外未修改任何文件，也未实际启动 0.18.3 server。
>
> 版本选择：镜像上 0.18.x 的最后一个稳定版是 **0.18.3**（之后是 `0.19.0rc*`），下文的“0.18”
> 均指 0.18.3。

## 结论（详见第 5 节）

**现在升。** 但必须按“独立分支 + 备份 lock/venv + 真 server 冒烟”的方式整体升级到
`langgraph-api 0.18.3` + `langgraph-runtime-inmem 0.38.3`，不要只改 `langgraph-api` 的 upper bound。
当前默认流量已经走 server（`backend/app/gateway/config.py:14`），继续拖在 0.7.103 上会让
唯一的生产路径长期停留在 11 个 minor 之前的运行时。

---

## 1) 当前钉的版本与约束

`backend/packages/harness/pyproject.toml:15-18`：

```toml
"langgraph-api>=0.7.0,<0.8.0",
"langgraph-cli>=0.4.14",
"langgraph-runtime-inmem>=0.22.1",
```

`langgraph-api` 有明确的 `<0.8.0` 上限，另外两个只有下限。实际锁与安装情况：

| 包 | 约束 | `uv.lock` 锁定 | 当前 `.venv` |
|---|---|---:|---:|
| `langgraph-api` | `>=0.7.0,<0.8.0` | **0.7.103**（`backend/uv.lock:1640`） | 0.7.103 |
| `langgraph-cli` | `>=0.4.14` | 0.4.43（`backend/uv.lock:1708`） | 0.4.43 |
| `langgraph-runtime-inmem` | `>=0.22.1` | **0.27.4**（`backend/uv.lock:1737`） | 0.27.4 |
| `langgraph-sdk` | 网关侧 `>=0.1.51`（`backend/pyproject.toml:11`） | 0.4.14（`backend/uv.lock:1755`） | 0.4.14 |
| `langgraph` | `>=1.2.0` | 1.2.22 | 1.2.22 |
| `langgraph-checkpoint` | 间接 | 4.3.2 | 4.3.2 |
| `langgraph-checkpoint-mongodb` | `>=0.2.0` | 0.5.1 | 0.5.1 |
| `cryptography` | 间接 | 46.0.7（`backend/uv.lock:486`） | 46.0.7 |
| `grpcio` | 间接 | 1.78.2（`backend/uv.lock:953`） | 1.78.2 |
| `grpcio-tools` | 间接 | 1.78.0（`backend/uv.lock:997`） | 1.78.0 |

两点注意：

- 仓库业务代码**没有直接 import** `langgraph_api` 或 `langgraph_runtime_inmem`
  （`rg` 全仓无命中），只通过 `langgraph dev`（CLI 拉起 server）和 `langgraph_sdk` HTTP 客户端使用。
- `langgraph-cli 0.4.43` 已是镜像上的最新版，其 `inmem` extra 允许 `langgraph-api<1.0.0,>=0.5.35`，
  升级不需要动 CLI。

---

## 2) 我们实际用到的 `langgraph_api` 能力

### 2.1 `langgraph.json` 的 `graphs` / `checkpointer`

`backend/langgraph.json:6-10`：

```json
"graphs": { "lead_agent": "erpflow.graph:make_lead_agent" },
"checkpointer": { "path": "./packages/harness/erpflow/checkpointer.py:make_checkpointer" }
```

- 图工厂：`backend/packages/harness/erpflow/graph.py:45`，签名 `make_lead_agent(config=None)`，
  单参数、无 `ServerRuntime` 注解 → 0.7.103 按 “1 参 = config” 处理
  （`site-packages/langgraph_api/_factory_utils.py:45 classify_factory` / `:91 _classify_factory`）。
- checkpointer：`backend/packages/harness/erpflow/checkpointer.py:20`，零参数、返回
  `MongoDBSaver`（失败回退 `InMemorySaver`）。server 侧加载点：
  `langgraph_api/_checkpointer/_adapter.py:347 collect_checkpointer_from_env`、
  `:407 _load_checkpointer`；接受 `BaseCheckpointSaver` 实例。
- 这是 server 启动的必经路径，`/ok`、Studio、所有 run 都依赖它。

### 2.2 `runs.stream` 的 `stream_mode` / `stream_subgraphs`

`backend/app/services/chat_stream.py`：

- `:35` `STREAM_MODES = ["messages-tuple", "values", "custom"]`
- `:218-224`

```python
stream = client.runs.stream(
    sdk_thread_id, ASSISTANT_ID,
    config=_server_config(...),
    stream_mode=STREAM_MODES,
    stream_subgraphs=True,
    **run_args,
)
```

服务端 0.7.103 对应逻辑：

- 默认 `stream_mode` 为 `["values"]`：`langgraph_api/models/run.py:171 assign_defaults`
- `stream_subgraphs` → `subgraphs`：`models/run.py:316`
- 流循环读取 `subgraphs`：`langgraph_api/stream.py:169`
- `messages-tuple` 在内部被移除并改写成 `messages`：`stream.py:206-207`、`:310-311`
- namespace 拼成 `"mode|ns"`：`stream.py:356-357`
- 传统（v1）SSE 路由：`langgraph_api/api/runs.py:988`

网关按 `|` 拆事件名的适配层：`backend/app/services/langgraph_adapter.py:22 split_event`、`:32 adapt_stream_part`。

### 2.3 `threads.create` / `threads.get_state`

- 建 thread（先映射成 UUID，`if_exists="do_nothing"`）：
  `backend/app/services/chat_stream.py:209`
- 查状态：`backend/app/gateway/routers/chat.py:80-82`
  ```python
  state = await get_client().threads.get_state(server_thread_id(thread_id))
  is_interrupted = bool(state.get("next")) if state else False
  ```
  中断项从 `state["tasks"][*]["interrupts"]` 取：`chat.py:102-110`。
- 服务端实现：`langgraph_api/api/threads.py:222 get_thread_state` →
  `langgraph_api/state.py:89 state_snapshot_to_thread_state`；
  inmem 侧 `langgraph_runtime_inmem/ops.py:1499 class State`。

### 2.4 Command 序列化

- 网关构造 **dict** 命令：`backend/app/services/chat_stream.py:41 _build_resume_payload`，
  `:63` 得到 `{"resume": ..., "update": {"_human_approved": ...}}`，作为 `command=` 传给 SDK。
- 服务端把 dict 还原成 `Command`：`langgraph_api/command.py:8 map_cmd`；
  类型定义 `langgraph_api/schema.py:263 RunCommand`。
- 这条链路要求 `update` 与 `resume` 同时支持，且 `update` 可以是 dict。

### 2.5 `--allow-blocking`

- 启动命令里用 `langgraph dev --allow-blocking`（实机记录见 `docs/refactor/reports/W1-live-smoke.md:3`）。
  注意 README/CLAUDE.md 里的命令**没有**带这个参数，冒烟时要手动加。
- CLI 只把它写成环境变量：`langgraph_api/cli.py:279`
  `LANGGRAPH_ALLOW_BLOCKING=str(allow_blocking).lower()`；
  真正消费在 `langgraph_runtime_inmem/queue.py:98`。

---

## 3) 0.7.103 → 0.18.3 的可能不兼容点

### 3.1 硬依赖门槛（确定，必须处理）

`langgraph_api-0.18.3.dist-info/METADATA` 的要求与当前环境对比：

| 依赖 | 0.18.3 要求（METADATA 行号） | 当前 | 结果 |
|---|---|---|---|
| `cryptography` | `>=50.0.0`（:10） | 46.0.7 | **不满足** |
| `grpcio` | `>=1.85.0,<1.86.0`（:13） | 1.78.2 | **不满足** |
| `grpcio-tools` | `==1.81.1`（:12） | 1.78.0 | **不满足** |
| `grpcio-health-checking` | `>=1.81.0,<1.82.0`（:11） | 1.78.2 | **不满足** |
| `langgraph-runtime-inmem` | `>=0.38.0.dev0,<0.39.0.dev0`（:20） | 0.27.4 | **不满足** |
| `opentelemetry-exporter-prometheus` | `>=0.63b1,<0.67`（:27） | 未安装 | **新增** |
| `prometheus-client` | 任意（:31） | 未安装 | **新增** |
| `langgraph` | `<2,>=0.4.10` | 1.2.22 | 满足 |
| `langgraph-checkpoint` | `<5,>=3.0.1` | 4.3.2 | 满足 |
| `langgraph-sdk` | `>=0.4.4` | 0.4.14 | 满足 |
| `starlette` / `mcp` | `>=1.3.1` / `<3,>=2.3.0` | 1.10.0 / 2.3.0 | 满足 |

镜像可用性已核对：`cryptography-50.0.2` 与 `grpcio-1.85.3` 都有 cp313 x86_64 manylinux wheel。
把 `langgraph-api==0.18.3`、`runtime-inmem==0.38.3`、`langgraph==1.2.22`、
`deepagents==0.7.34`、`langchain==1.5.0`、`mcp==2.3.0` 等一起做临时解析可以成功，
最终会得到 `cryptography 50.0.2`、`grpcio 1.85.3`、`grpcio-tools 1.81.1`、`protobuf 6.33.6`。

### 3.2 六个能力面的逐项核对（均为兼容）

| 能力 | 0.7.103 | 0.18.3 | 判定 |
|---|---|---|---|
| `graphs` 工厂（0/1/2 参，单参为 config） | `_factory_utils.py:45,91` | `_factory_utils.py:46,92`，分类逻辑逐行一致 | 兼容：`make_lead_agent(config)` 仍按 config 注入 |
| `checkpointer.path` 加载 | `_adapter.py:347,407` | `_adapter.py:385,445`，块内容逐行一致 | 兼容 |
| 自定义 checkpointer 能力探测 | `_adapter.py:73-90` | `_adapter.py:81-98`，字段与 `_is_overridden` 相同 | 兼容；`MongoDBSaver 0.5.1` 的 `adelete_thread/adelete_for_runs/acopy_thread/aprune` 均存在（运行时核对过） |
| `stream_mode` 取值 | `schema.py:18`（9 个） | `schema.py:20`（新增 `tools`、`lifecycle`，无删除） | 兼容 |
| `messages-tuple` 改写为 `messages` | `stream.py:206-207,310-311` | `stream.py:281-282,448-449` | 兼容 |
| `stream_subgraphs` → `subgraphs` | `models/run.py:316`；`stream.py:169` | `models/run.py:326`；`stream.py:187` | 兼容 |
| namespace `"mode|ns"` 拼接 | `stream.py:356-357` | `stream.py:660-667` | 兼容：网关 `split_event` 无需改 |
| v1 SSE 路由 | `api/runs.py:988` | `api/runs.py:973`，仍注册 | 兼容：SDK 0.4.14 `runs.stream` 的 `version` 默认 `"v1"`（`langgraph_sdk/_async/runs.py:234`） |
| `threads.create` | `api/threads.py:55` | `api/threads.py:59`；仍校验 UUID、仍支持 `if_exists` | 兼容：我们显式传 UUID |
| `threads.get_state` | `api/threads.py:222`、`state.py:89` | `api/threads.py:218`、`state.py:100`，返回键 `values/next/tasks/metadata/interrupts/...` 一致 | 兼容 |
| `RunCommand` / `map_cmd` | `schema.py:263`、`command.py:8` | `schema.py:282`、`command.py:8` | 兼容：`command.py` 两次 diff **完全相同** |
| `--allow-blocking` | `cli.py:279`、`runtime_inmem/queue.py:98` | `cli.py:290`、`queue.py:98` | 兼容 |
| `run_server` 参数 | `cli.py:168-199` | `cli.py:176-208` | 兼容：CLI 0.4.43 传入的参数在两边都存在 |

### 3.3 行为差异（低风险，但要知道）

1. **v2 事件流默认开启**：`feature_flags.py:30` 的 `FF_V2_EVENT_STREAMING` 默认 `true`，
   `api/__init__.py:191-206` 会额外注册 v2 路由。但 v1 的 `runs_routes` 仍无条件注册
   （`api/__init__.py:198-206` 的 else 分支），SDK 0.4.14 默认 `version="v1"`，我们不受影响。
   若以后 SDK 大版本把默认改成 v2，`stream.py:259-266` 明确说明 v2 的 `messages` 线格式与 v1 **不兼容**，
   网关的 `"mode|ns"` 解析会失效。因此建议把 SDK 上限钉在 `<0.5`。
2. **`lifecycle` 只允许 v2 路由**：`models/run.py:241-252` 对 legacy 路由直接 422；我们没请求它。
3. **中断项多了可选 `response_schema`**：`state.py:67-79`。网关 `unwrap_interrupt`
   （`langgraph_adapter.py:68`）只取 `value`，多一个键无害。
4. **`threads.create` 生成的默认 id 从 `uuid4()` 改成 `uuid7()`**（`api/threads.py:67`）；
   我们总是显式传 `thread_id`，无影响。
5. **`/ok` 返回体变化**：0.18 `api/__init__.py:90` 起会返回 `{"ok":..., "services":{...}}`，
   依赖检查失败时返回 503/500；0.7 是裸 `{"ok": True}`。用 `curl -f` 的冒烟不受影响，
   但若有脚本按 body 断言需要注意。
6. **gRPC checkpointer 开关**：`PREFER_GRPC_CHECKPOINTER` 只对 postgres 生效
   （`feature_flags.py:61` 注释、`_adapter.py:354`）；inmem 和自定义 checkpointer 不受影响。
7. **inmem 本地持久化有迁移**：0.38.3 读旧 `.langgraph_ops.pckl` 时会补
   `thread_has_next/thread_failed/run_priority/fairness_group` 等字段，并把无法反序列化的文件
   改名备份（`langgraph_runtime_inmem/database.py:162-234`，调用点 `:319-325`）。
   旧 ops 文件可继续用；但 `.langgraph_checkpoint.*.pckl` 没有版本标记，见 3.4。

### 3.4 无法仅靠源码确认的项（不做推测）

- **未实测**：0.18.3 + `deepagents 0.7.34` / `langchain 1.5.0` / 现有中间件与沙箱的完整运行行为。
- **未实测**：`.langgraph_checkpoint.*.pckl` 跨 0.27.4 → 0.38.3 的反序列化。ops 文件有迁移代码，
  checkpoint 文件没有显式迁移；本工作区里的 checkpoint 文件只有 6 字节（空），风险低，但**不能确认**。
- **未实测**：Mongo 中由 0.7.103 server 写入的 checkpoint 能否被 0.18.3 读取。
  理论上格式由未变动的 `langgraph-checkpoint 4.3.2` + `langgraph-checkpoint-mongodb 0.5.1` 决定，
  但没有做真库读写验证。
- **未实测**：LangGraph Studio 与 0.18 的 UI/协议兼容性（仓库前端不直连 2024，只经网关，影响面小）。
- **未找到**官方 0.7 → 0.18 的逐版本迁移说明；以上均为两个 wheel 的源码对比结论。
- 旧版本中我们**没有使用**的内部模块被删除（`executor_entrypoint.py`、`metrics_datadog.py`、
  `FF_USE_JS_API`、`sse_heartbeat` 等），已核对确实不存在于 0.18.3，但不构成我们的不兼容点。

---

## 4) 升级步骤与回滚

### 4.1 升级步骤

当前工作区有大量未提交改动，**先不要直接在这些改动上重锁依赖**。

```bash
cd /home/snape/github_repo/ERP-AGENT-open-source
git switch -c chore/langgraph-api-0.18     # 或在干净工作树上操作

cd backend
# 0. 备份（不要只依赖 git）
cp uv.lock /tmp/uv.lock.0.7.103
cp pyproject.toml /tmp/backend-pyproject.0.7.103
cp packages/harness/pyproject.toml /tmp/harness-pyproject.0.7.103
cp -a .langgraph_api /tmp/langgraph_api.0.7.103
```

1. 修改 `backend/packages/harness/pyproject.toml:16-18`：
   ```toml
   "langgraph-api>=0.18.3,<0.19.0",
   "langgraph-cli>=0.4.43,<0.5.0",
   "langgraph-runtime-inmem>=0.38.3,<0.39.0",
   ```
2. 修改 `backend/pyproject.toml:11`，给网关侧的 SDK 加上限（理由见 3.3 第 1 条）：
   ```toml
   "langgraph-sdk>=0.4.14,<0.5.0",
   ```
3. 只定向升级这几个包，避免顺带漂移 `deepagents` 等：
   ```bash
   uv lock --upgrade-package langgraph-api \
            --upgrade-package langgraph-runtime-inmem \
            --upgrade-package langgraph-cli
   uv sync
   ```
   预期同时升级 `cryptography`、`grpcio`、`grpcio-tools`、`grpcio-health-checking`，
   新增 `opentelemetry-exporter-prometheus`、`prometheus-client`。
4. 核对结果：
   ```bash
   uv run python -c "import importlib.metadata as m; print(m.version('langgraph-api'), m.version('langgraph-runtime-inmem'))"
   ```
5. 首次启动前，把旧 inmem 缓存挪走（确认 3.4 的 checkpoint 风险不落到本次冒烟里）：
   ```bash
   mv .langgraph_api /tmp/langgraph_api.pre018
   ```
6. 起 server 并做真实冒烟（不要只跑 FakeClient 单测）：
   ```bash
   uv run langgraph dev --no-browser --no-reload --allow-blocking --port 2030
   curl -fsS http://127.0.0.1:2030/ok
   ```
   日志必须确认：`lead_agent` 注册成功；`Checkpointer: MongoDB`（或明确的 InMemory 回退）；
   没有 `aprune` / `acopy_thread` / `adelete_*` 能力告警。
7. 网关侧冒烟（`LANGGRAPH_URL=http://127.0.0.1:2030`）：
   - 新 thread → run，确认 `token` / `done` 与升级前一致；
   - 确认子图事件 namespace 正常（`langgraph_adapter.split_event` 能拆出非空 ns）；
   - 触发 HITL 中断 → `GET /api/chat/{id}/state` 返回 `interrupted=true` 且有审批卡片；
   - 用 dict `command` resume，确认不重复写 `_human_approved`（不能出现
     `InvalidUpdateError: Can receive only one value per step`）。
8. 回归：
   ```bash
   uv run pytest tests/ -q
   uv run ruff check .
   ```
9. 全部通过后再提交；`AGENT_BACKEND` 已经是 `server`（`app/gateway/config.py:14`），不需要再切默认值。

### 4.2 回滚

```bash
cd backend
# 停掉 langgraph dev 与网关

cp /tmp/uv.lock.0.7.103 uv.lock
cp /tmp/backend-pyproject.0.7.103 pyproject.toml
cp /tmp/harness-pyproject.0.7.103 packages/harness/pyproject.toml

uv sync --frozen
```

- 如果 0.18 已经写过 `.langgraph_api`：删掉它并恢复备份
  （`mv .langgraph_api /tmp/langgraph_api.018attempt && cp -a /tmp/langgraph_api.0.7.103 .langgraph_api`）。
  这只影响本地开发缓存，不影响 Mongo 中的业务会话。
- 网关代码不需要回滚：它只依赖 `langgraph_sdk 0.4.14`，与 server 0.7.103 / 0.18.3 都兼容。
- 回滚后重启两个进程即可；Mongo checkpoint 不需要迁移。

---

## 5) 最终结论

**现在升。**

理由：

1. **六个真实依赖面全部逐符号核对为兼容**：图工厂、`checkpointer.path`、`stream_mode`/`stream_subgraphs`、
   `messages-tuple` 改写、`threads.get_state`、`Command` 序列化。`command.py` 在两个版本中
   完全一致，`state_snapshot_to_thread_state` 只多了一个可选字段。
2. **升级成本是确定的依赖工作，不是 API 改造**：`cryptography 46→50`、`grpcio 1.78→1.85`、
   `runtime-inmem 0.27→0.38` 都有 cp313 wheel，且临时解析已经成功。
3. **现在风险窗口最小**：默认流量已经是 server（`config.py:14`），0.7.103 是唯一在跑的路径；
   越晚升级，后续任何 server 侧改动都会叠加在未验证的旧运行时上。
4. **仓库没有生产部署**：`compose.yaml` 只有 mongo/postgres/sandbox，没有 LangGraph Server 镜像；
   server 只在本机 `langgraph dev` 跑，回滚成本低。
5. **真正的风险是“没做真集成测试”，不是版本不兼容**：`tests/test_chat_stream_sdk.py` 用 `FakeClient`，
   只验证调用形状。所以升级的门槛不是“能不能升”，而是“必须做一次第 4.1 节第 6-7 步的真冒烟”。

唯一例外：如果本周有不可延期、必须绝对稳定的演示，可以把升级排到演示之后；但不要长期停在
0.7.103，也不要把 `langgraph-api` 单独升到 0.18 却继续配 0.27 的 runtime（0.18.3 的 METADATA
明确要求 `runtime-inmem>=0.38.0.dev0`）。

W3_DONE

# W1a — 事件映射设计报告（只读）

**日期**：2026-11-18
**范围**：为 W1「网关从进程内 Agent 切到 `langgraph-sdk`」提供事件映射、API 形态与风险清单。

**结论基线**：本报告依据仓库当前锁定的 `langgraph-sdk 0.4.14`、`langgraph-api 0.7.103`、`langgraph 1.2.22`（`backend/uv.lock:1641`、`backend/uv.lock:1756`、`backend/uv.lock:1623-1624`）与仓库内网关代码。`deerflow2.0-enhanced` 不在本仓库内，实际路径为 `/home/snape/github_repo/deerflow2.0-enhanced/backend/app/channels/manager.py`（本报告已核对 566-577 行）。

**总判断**：12 个现有 SSE 事件全部可以保留，但**不能**把 `StreamPart` 直接喂给现有 `ChatTurn`。必须加一层适配，处理四件事：namespace 在事件名里、消息已序列化成 dict、中断项多一层 `{"value","id"}` 包装、`/state` 返回 dict。`thinking(start)`、`tool_end`、`done` 本来就是网关侧合成，继续由网关生成。

---

## 1. 输入事实

- 参考实现确实在仓库外，且它自己的事件判断有 bug：

```python
# deerflow2.0-enhanced/backend/app/channels/manager.py:566-577
async for chunk in client.runs.stream(
    thread_id, assistant_id,
    input={"messages": [{"role": "human", "content": msg.text}]},
    config=run_config, context=run_context,
    stream_mode=["messages-tuple", "values"],
):
    event = getattr(chunk, "event", "")
    if event == "messages-tuple":   # 永远不成立
```

- `messages-tuple` 是**请求参数**，不是服务端发出的 event 名。服务端会把请求里的 `messages-tuple` 改写成内部 `messages` 模式（`API/stream.py:206-208`），最终发出的 event 是 `messages` 或 `messages|<ns>`（`API/stream.py:309-314`）。
- 所以「参考实现怎么写的」不能作为映射依据，只能作为反例。本报告第 2 节的结论全部来自 SDK/API 源码。

引用约定：`SDK/` = `backend/.venv/lib/python3.13/site-packages/langgraph_sdk/`；`API/` = `backend/.venv/lib/python3.13/site-packages/langgraph_api/`。

---

## 2. 问题 1：`runs.stream` 的流模式、返回结构与 namespace

### 2.1 支持的 `stream_mode`

`SDK/schema.py:51-63` 定义 11 个值：

```text
values, messages, updates, events, tasks, checkpoints,
debug, custom, messages-tuple, tools, lifecycle
```

W1 需要的组合只有三个：

```python
stream_mode=["messages-tuple", "values", "custom"]
stream_subgraphs=True
```

`messages-tuple` 是唯一能拿到「逐 chunk 消息 + metadata」的客户端模式；请求 `messages` 会得到聚合后的 `messages/partial` / `messages/complete`（`API/stream.py:315-354`），metadata 不再随每个 chunk 下发。两者不能混用。

### 2.2 `StreamPart` 结构

```python
class StreamPart(NamedTuple):
    event: str          # 事件名；子图带 "|<ns>" 后缀
    data: Any           # 解码后的 JSON
    id: str | None = None
```

引用：`SDK/schema.py:619-627`；SSE 解码处同构构造：`SDK/sse.py:124-128`。

`data` 的实际形状：

| mode | `data` 形状 |
|---|---|
| `values` | `dict`：完整状态快照；中断时含 `__interrupt__` |
| `messages` | `list`：`[message_dict, metadata_dict]`；`message` 已由 LangChain 对象序列化成 dict |
| `custom` | 中间件 `stream_writer` 写入的 JSON 值；本仓库是 `dict` |
| `metadata` | `{"run_id": ..., "attempt": ...}`（`API/stream.py:234-235`） |
| `end` | `{"run_id": ..., "status": ...}`（SDK 文档示例：`SDK/_async/runs.py:312-315`） |
| `error` | `{"error": <异常类名>, "message": <文本>}`（`API/sse.py:87-95` + `API/serde.py:87-110`） |

消息变成 dict 的依据：SSE 用 `json_dumpb` 序列化，`json_dumpb` 走 `default=default`（`API/serde.py:168-174`），而 `default` 对带 `model_dump()` 的对象直接调用 `model_dump()`（`API/serde.py:70-82`）。

### 2.3 `subgraphs=True` 时 namespace 从哪里取

- SDK 参数名是 `stream_subgraphs`，**不是** `subgraphs`（`SDK/_async/runs.py:211`）。
- 服务端把 namespace 拼进事件名：

```text
<mode>|<ns1>|<ns2>...
```

依据：`API/stream.py:309-314`（messages）、`API/stream.py:355-359`（values/custom 等）。
- 提取方式：`mode, *ns = part.event.split("|")`；主图 `ns == []`，子图 `ns == ["analyst:<task_id>"]`。
- v2 格式（`version="v2"`）返回 `{"type", "ns", "data"}`，其换算函数同样按 `|` 切分（`SDK/_shared/utilities.py:116-131`）；但 v2 会把 `__interrupt__` 从 `data` 弹出放到单独的 `interrupts` 字段（同文件 127-128 行）。W1 建议用默认 v1：v1 的 `values.data` 仍保留 `__interrupt__`，与现有 `ChatTurn` 最接近。

---

## 3. 问题 2：12 个 SSE 事件的逐条映射

现有事件集合与线格式：`backend/app/services/chat_turn.py`、`chat_stream.py`、`sse.py:22-24`。

判定口径：**等价** = 数据形状与产生条件不变；**需适配** = 语义不变但取值方式要改；**语义差异** = 行为不完全一致，W1 必须做决定。

| # | SSE 事件 | 现有产生点 | SDK 来源（mode / event / 取值路径） | 差异与 W1 改点 | 判定 |
|---|---|---|---|---|---|
| 1 | `thinking` | `chat_stream.py:92,102` 发两次 `start`；`chat_turn.py:243-245,293-296` 发 `end` | SDK 无对应事件 | `start` 继续网关本地合成；`end` 仍由「首条可见 content 或 `tool_call_chunks`」触发，只把对象访问改成 dict 访问 | 等价（本地合成） |
| 2 | `token` | `chat_turn.py:287-305`，`source` 来自 `source_label` | `messages` / `messages|<ns>`；`data[0]["content"]` | `data[0]` 是 dict；`source` 要用 `event.split("|")[1:]` 计算。`type` 序列化后是 `"AIMessageChunk"`（`langchain_core/messages/ai.py:424`），现有 `token_type in ("ai","AIMessageChunk")`（`chat_turn.py:283`）刚好兼容 | 需适配 |
| 3 | `tool_start` | `chat_turn.py:242-262` | 同一条 `messages` 事件；`data[0]["tool_call_chunks"][i]["name"]` / `["id"]` | `tool_call_chunks` **仍在流里**（`AIMessageChunk` 的正式字段：`langchain_core/messages/ai.py:427`，`model_dump()` 会带出），但每项是 dict | 需适配 |
| 4 | `tool_args` | `chat_turn.py:263-266` | 同上；`tool_call_chunks[i]["args"]` | 仍是增量字符串；dict 取值，累加逻辑不变 | 需适配 |
| 5 | `tool_result` | `chat_turn.py:269-272,308-358` | `messages` 事件中的 ToolMessage；`data[0]["name"]`、`["tool_call_id"]`、`["content"]` | dict 顶层取值；`content` 可能是 list，现有 `str()` 兜底可用 | 需适配 |
| 6 | `tool_end` | `chat_turn.py:359` | 无 | 继续在 `tool_result` 后本地合成 | 等价（本地合成） |
| 7 | `phase` | `chat_turn.py:147-159`（`values.phase`）；`chat_turn.py:203-209`（custom 进入 reviewing） | `values` 或 `values|<ns>`；`data["phase"]`；`custom` 的 `rubric_evaluation_start` | `values.data` 本来就是 dict；`custom` 事件名带 ns，匹配要用 `split("|")[0]` | 需适配 |
| 8 | `todo_update` | `chat_turn.py:161-176`（`values.todos`）；`chat_turn.py:335-352`（todo/task 工具） | `values`；`data["todos"]`；或 ToolMessage 名字启发式 | `todos` 是 dict 列表，取值不变 | 等价 |
| 9 | `review_result` | `chat_turn.py:210-222` | `custom` / `custom|<ns>`；`data["type"] == "rubric_evaluation_end"` | payload 透传（见 R3）；**不能**写 `event == "custom"` 全等判断 | 需适配 |
| 10 | `interrupt` | `chat_turn.py:178-193` → `hitl.build_interrupt_event`（`hitl.py:107-129`） | `values`；`data["__interrupt__"]` | **语义差异**：SDK 里每项是 `{"value": <中断值>, "id": ...}`（`SDK/schema.py:295-303`）。现有 `hasattr(item,"value")`（`chat_turn.py:182-186`）在 dict 上失败，会把包装 dict 传给 `build_interrupt_event`，审批卡片拿不到 `action_requests` | 语义差异 |
| 11 | `done` | `chat_stream.py:116,132,137`（本地） | SDK `end` 事件 | 保留本地 `done`；不能把 `end` 直接转发，也不能在收到 `end` 后再补一个。`end.status` 非 success 时不能按「正常完成」收尾 | 语义差异 |
| 12 | `error` | `chat_stream.py:134-136`（仅异常路径） | SDK `error` 事件 + 迭代抛出的异常 | 服务端把运行错误当**正常事件**下发（`API/sse.py:87-95`），只 `except Exception` 会漏掉它，前端永远收不到错误；payload 需统一成现有 `{"message": ...}` | 语义差异 |

### 3.1 无法直接映射的项（必须网关合成）

- `thinking` 的 `start`（`chat_stream.py:92,102`）
- `tool_end`（`chat_turn.py:359`）
- `done`（`chat_stream.py:116,132,137`）
- `error` 的网关统一壳（SDK 的 `error` 只作为触发源）

其余 8 个事件都有明确 SDK 来源，没有「无法映射」项。

### 3.2 必须改适配层、否则静默丢功能的项

1. **消息对象 → dict**：`getattr(token, "type")`、`getattr(token, "tool_call_chunks")`、`getattr(token, "name")`、`getattr(token, "tool_call_id")`（`chat_turn.py:237-239,277,311,317`）在 SDK 流里全部返回默认值。后果：`token`、`tool_start`、`tool_args`、`tool_result` 全消失，且不报错。
2. **namespace → 事件名后缀**：不拆 `|` 的话 `source` 全部标成 `main`（`chat_turn.py:366-375`），且 `review_decision` 的主图判断 `not namespace`（`chat_turn.py:136`）会把子图判定当主图决策。
3. **中断包装**：见 #10。
4. **`review_router` 过滤**：`metadata` 现在是 `data[1]` 的 dict；不原样传下去，`chat_turn.py:229-231` 的 `tags` 过滤失效，评审器的英文输出会泄露到前端。

---

## 4. 问题 3：中断恢复与 `/state`

### 4.1 resume 的 SDK 写法

现有 `_build_resume_input` 返回 `langgraph.types.Command`（`chat_stream.py:34-59`）。SDK 的 `command` 参数是 **TypedDict**（`SDK/schema.py:914-938`），发送前执行 `command.items()`（`SDK/_async/runs.py:328-330`）。`langgraph.types.Command` 是 dataclass，没有 `.items()`，**直接传会 `AttributeError`**。

正确形态：

```python
command = {"resume": resume_value}
if decisions:                      # 仅在 decisions 非空时
    command["update"] = {"_human_approved": is_approved(decisions)}

await client.runs.stream(
    thread_id,
    "lead_agent",                  # langgraph.json:6-8
    command=command,
    config={"configurable": {"user_id": user_id, "username": username}},
    stream_mode=["messages-tuple", "values", "custom"],
    stream_subgraphs=True,
    if_not_exists="create",
    on_disconnect="cancel",
    version="v1",
)
```

要点：

1. `input` 与 `command` 互斥（`SDK/_async/runs.py:244`），resume 不再传 `input`。
2. 服务端用 `map_cmd` 把 dict 还原成 `Command(update=..., goto=..., resume=...)`（`API/command.py:8-31`），所以 `_human_approved` 与 `resume` 同 super-step 生效的行为不变。
3. 「只有带 decisions 才写 `_human_approved`」必须保留，原因仍是同一步二次写入抛 `InvalidUpdateError`（`chat_stream.py:39-42,56-59`）。
4. `normalize_edit_decisions`（`hitl.py:42-66`）与 `resolve_part_codes`（`chat_stream.py:52-54`）继续留在网关，输入形状不变。

### 4.2 `/state` 的调用形态

```python
state = await client.threads.get_state(thread_id)
```

- 签名：`SDK/_async/threads.py:486-496`，返回 `ThreadState`。
- 结构：`{"values", "next", "tasks", "interrupts", "checkpoint", "metadata", ...}`（`SDK/schema.py:339-358`）；`ThreadTask` 也是 dict，`interrupts: list[Interrupt]`（`SDK/schema.py:327-337`）；`Interrupt = {"value", "id", "response_schema"?}`（`SDK/schema.py:295-303`）。
- 服务端响应同时填顶层 `interrupts` 与每个 task 的 `interrupts`（`API/state.py:89-121`）。
- 现有实现有三处必然失效：
  - `bool(getattr(state, "next", None))`（`chat.py:82`）→ dict 无属性，恒 `False`；
  - `getattr(task, "interrupts", ())`（`chat.py:107-109`）→ 永远拿不到；
  - `getattr(item, "value", item)`（`chat.py:109-110`）→ 拿到包装 dict，卡片为空。
- 改法：`bool(state.get("next"))`；遍历 `state["tasks"][*]["interrupts"]` 取 `item["value"]`。顶层 `state["interrupts"]` 可用，但 task 级更贴近现有「取第一个任务中断」的语义。

### 4.3 thread 不存在时的行为（重要）

`runs.stream` 默认 `if_not_exists="reject"`（`SDK/_async/runs.py:265-266`）。服务端遇到「thread 不存在且未要求创建」时返回**空生成器**，不抛异常：

```python
# langgraph_runtime_inmem/ops.py:2445-2499
if not existing_thread and (thread_id is None or if_not_exists == "create"):
    ...
else:
    return _empty_generator()
```

后果不是报错，而是**静默空流**：网关会发 `thinking` 然后直接 `done`，用户看到空回答。现有 thread_id 只存在网关 Mongo 里，服务端没有对应 thread，所以 W1 必须二选一：

- `runs.stream(..., if_not_exists="create")`；
- 或首次 run 前 `client.threads.create(thread_id=..., if_exists="do_nothing")`（`SDK/_async/threads.py:102-121`）。

`threads.get_state` 对不存在的 thread 则返回 404（HTTP 异常），现有 `except Exception` 可以兜住，但会把「thread 不存在」与「真的没中断」混为一谈。

---

## 5. 问题 4：风险清单

影响面标注：**前端** = 用户可见；**资源** = 容器/内存/延迟；**数据** = checkpoint / 展示历史一致性。

| # | 风险 | 事实与证据 | 影响面 | 处置结论 |
|---|---|---|---|---|
| R1 | `tool_call_chunks` 仍可得，但形状变了 | 字段随 `model_dump()` 下发（`langchain_core/messages/ai.py:427`、`API/serde.py:70-82`）；客户端拿到 dict | 前端：沿用 `getattr` 会让 `tool_start`/`tool_args`/`tool_result`/`tool_end` 与持久化的 `toolCalls` **全部静默为空** | 适配层统一按 dict 取值，或显式 `AIMessageChunk(**d)` 还原；补 dict 输入的单测 |
| R2 | `messages` 模式选择 | 请求 `messages-tuple` 才返回 `(chunk, metadata)`（`API/stream.py:206-208,309-314`）；请求 `messages` 返回聚合消息（`API/stream.py:315-354`） | 前端：用错模式则 `metadata.tags` 过滤失效，`review_router` 的英文判定与结构化工具调用会泄露到前端 | 固定请求 `messages-tuple`；测试断言 `data[1]["tags"]` 含 `review_router` |
| R3 | `metadata.tags` 是否保留 | langgraph 在 messages 元数据里显式写 `metadata["tags"] = filter_to_user_tags(tags)`（`langgraph/pregel/_messages.py:167-173`）；`review_router` 由 `config={"tags": [...]}` 注入（`review/policy.py:142,157`） | 前端：评审路由器的内部输出可见性 | 保留现有过滤；不要把 `data[1]` 丢掉或重命名 |
| R4 | `custom` 是否透传 | `custom` 与 `values` 走同一拼接分支（`API/stream.py:355-359`）；rubric 通过 `runtime.stream_writer` 写事件（`deepagents/middleware/rubric.py:1336-1355`，事件名 736/798/1310） | 前端：`review_result` 与 `reviewing` 阶段消失 | 用 `event.split("|", 1)[0] == "custom"` 匹配；payload 原样交给 `handle_custom` |
| R5 | per-user 上下文传参 | 图工厂只接收 `config`（`graph.py:45-51`）；服务端工厂分派只支持 `config` / `ServerRuntime`（`API/_factory_utils.py:91-140`）；`context=` 只在图已建好后传给 `graph.astream(context=...)`（`API/stream.py:171,260-261,377-378`） | 资源 + 前端：放 `context=` 则所有用户落到 `default_user`（`graph.py:49`），共享沙箱与命名空间；`username` 缺失会让提示词永远显示「用户」（`agent/build.py:286-292`） | 必须放 `config.configurable`，并补 `username`；现有 `create_config` 只传 `user_id`（`agent_session.py:156-158`），W1 必须同时改。若确实要用 `context`，工厂需改为 `(config, runtime: ServerRuntime)` 并给图加 `context_schema`（`SDK/runtime.py:148-163`） |
| R6 | 沙箱预热池双份维护，且会互删 | 网关 lifespan 补池 + 维护线程 + 关停销毁 warm（`app/gateway/app.py:77-87,104-109`）；harness 首次建图也起维护线程（`graph.py:28-52`、`pool_maintenance.py:29-47`）。`cleanup_stale_warm` 删除**所有未在 Mongo 登记的 warm**（`sandbox_reaper.py:22-35`），而 warm 只在被认领后才 `_persist`（`sandbox_manager.py:184-194`） | 资源：容器数翻倍；**网关每次重启都会删掉 server 正在使用的预热池**，首请求冷启动 | 池只归 server 进程：删除网关 lifespan 沙箱段、`bind_sandbox`/`acquire_lease`（`chat_stream.py:97-99,151-154`）；只保留 `erpflow.backends.pool_maintenance` |
| R7 | `display_messages` 节流持久化 | 展示消息由网关 Mongo 仓库维护（`repositories/conversations.py:53-70`），节流/兜底在 `chat_turn.py:95-100,142-150`；与 Agent 所在进程无关 | 数据：切 SDK 后仍可在网关完成，不需要服务端参与 | 保留「网关消费事件 + `shield` 兜底」；`/history` 继续读网关 Mongo，不要改成读 checkpointer |
| R8 | 断连后 run 继续跑 | SDK 默认 `on_disconnect=None`，服务端默认 `continue`（`API/api/runs.py:326`，`cancel_on_disconnect = on_disconnect == "cancel"`：同文件 352 行） | 数据：客户端断开后 run 仍跑完，网关已停止消费 → `display_messages` 停在断连点，checkpoint 却是完整答案；下一轮还可能与该 run 并发 | 显式 `on_disconnect="cancel"`，与现在「断流即取消生成器」一致；若选 `continue`，必须接受展示历史截断并做补偿 |
| R9 | `runtime_context` 在 server 进程无人绑定 | `bind_runtime` 只在网关调用（`chat_stream.py:99`）；`memory_tools.py:33-39`、`web_fetch.py:26`、`skill_management.py:7` 读 ContextVar，默认 `default_user`/`None`（`runtime_context.py:11-16`） | 功能静默降级：记忆检索报「没有可用 Store」，技能/文件工具拿不到 store 与用户身份 | 在 server 侧按 run 绑定（图工厂或中间件）。这是 **W1 的功能前置**，不能推迟 |
| R10 | `/state` 每次都重建图并认领沙箱，且可能落到 `default_user` | `threads.get_state` 以 `access_context="threads.read"` 调图工厂（`API/graph.py:336-376`、`SDK/runtime.py:76-88`；inmem 实现：`langgraph_runtime_inmem/ops.py:1551-1561`），工厂内部会 `create_main_agent` → `sandbox_manager.get_sandbox`（`build.py:120`）。`user_id` 只来自 thread 存储的 configurable（`ops.py:1535-1541`）：由首轮 run 自动建 thread 时会带上；**用 `threads.create` 建的 thread、以及 W1 迁移时服务端新建的历史 thread 没有该键，必然回落到 `default_user`**（`graph.py:49`） | 资源：每次刷新页面都重建 agent 并做沙箱健康检查，首屏变慢；迁移后的历史会话会额外认领一个 512M 的 `default_user` 沙箱 | 工厂读 `configurable["__is_for_execution__"]`（`API/graph.py:371`），非执行上下文不认领沙箱——这是唯一可靠的做法。`threads.create` **没有 config 参数**（`SDK/_async/threads.py:102-113`），不能靠建 thread 写 `user_id`；历史 thread 的 `user_id` 只能在首轮 run 时随 config 落库 |
| R11 | 并发锁 | `run_lock` 只在网关进程内（`chat_stream.py:96`）；多 worker/多副本失效；ROADMAP 计划删 `agent_sessions`（`ROADMAP.md:49`）后锁彻底消失 | 数据：同 thread 并发 run，checkpoint 互相覆盖 | 显式 `multitask_strategy="reject"`；网关侧把锁改成按 `thread_id` 的锁表，不要按 `user_id` |
| R12 | `values` 负载膨胀 | 每个 step 推完整 state，`messages` 是全历史（`API/stream.py:355-359`）；进程内原本只传对象引用 | 资源：长会话 O(n²) 带宽与 JSON 解析；可能触发服务端流队列丢数据（`API/stream.py:36` 的 `LSD_PUBLISH_QUEUE_SIZE`） | W1 先做 50~100 轮基线；不可接受则用 `updates`/`tasks` 派生 phase/todos，只保留中断检测所需的最小 values |
| R13 | 现有测试直接失效 | `tests/test_chat_interrupts.py:14-17` mock 的是 `session.agent.astream`；切换后 `agent_sessions` 被移除 | 回归：12 个事件与中断卡片失去自动化保护 | 新增 adapter 单测（`StreamPart` 列表 → 12 事件序列）；旧用例改造成 adapter 用例，不要删 |
| R14 | 服务端子图中断的事件名有拼接缺陷 | `API/stream.py:368-371` 在「仅中断 updates」分支写的是 `yield "values|{'|'.join(ns)}"`，**没有 `f` 前缀** | 前端：若中断发生在子图，event 名是字面量 `values|{'|'.join(ns)}`，适配层会解析出错误 ns，中断可能被误判为主图或无法识别 | 当前 HITL 中断在主图，不触发；适配层对无法识别的 ns 保守回退，并在集成测试覆盖子图中断。该缺陷属于服务端，记入报告不在 W1 修 |
| R15 | 版本耦合 | `langgraph-sdk 0.4.14` 与 `langgraph-api 0.7.103` 由 `uv.lock` 锁死（`backend/uv.lock:1641,1756`）；`0.7.103` 已 EOL（`ROADMAP.md:58`） | 升级：`stream_subgraphs`、`context`、`strict_resume` 都是双侧协议 | W1 不动版本；升级留给 W3，必须两侧同时动 |

补充两条部署面事实：

- LangGraph Server 默认不做会话归属校验（thread 权限取决于部署的 auth）。网关现有的 `get_conversation_user_id` 校验（`chat.py:29-31,55-57`）必须保留，否则知道 thread_id 就能读别人的会话。
- 网关仍然需要 Mongo：`display_messages`、会话归属、标题都在 Mongo，切 SDK 后不会消失。

---

## 6. 问题 5：最小改动切换路径（三步，各自可验证、可回滚）

### 第 1 步：只加适配层，不切生产流量

- 新增 `StreamPart → (namespace: list[str], mode: str, chunk)` 适配：
  - `mode = part.event.split("|")[0]`；
  - `namespace = part.event.split("|")[1:]`；
  - `messages` 的 `chunk` 保持 `[message_dict, metadata]`；
  - `values` / `custom` 原样透传；
  - 丢弃 `metadata`、`end`、心跳。
- 录一份真实 SDK 流（纯文本、工具调用、子图文本、中断、`rubric_evaluation_end`）作 fixture，用现有 `ChatTurn` 跑对比，断言 12 个事件的名字、顺序、关键字段与进程内路径一致。
- **独立验证**：`token.source` 正确；`tool_start/args/result/end` 非空；`review_router` 文本被过滤；`interrupt` 的 `tool_name`/`tool_args` 正确；`done`/`error` 仍由网关生成。
- **回滚**：保留原 `session.agent.astream` 分支，环境变量二选一。

### 第 2 步：切换流读取、resume、`/state`

- `get_client(url=LANGGRAPH_URL)` 替换 `session.agent.astream` / `aget_state`；
- 新消息 `input={"messages":[{"role":"user","content":message}]}`；恢复 `command={...}`；
- `config={"configurable":{"user_id","username"}}`，不再传 `context`；
- `if_not_exists="create"`（或先 `threads.create`），否则新会话静默空流；
- `/state` 改 `threads.get_state(thread_id)`，全部 dict 访问并解包 `["value"]`；
- **独立验证**：普通对话、工具调用、子 Agent 文本、审批中断、resume、刷新取卡片、客户端中途断开七项全过；同一中断重复 resume 不抛 `InvalidUpdateError`；新 thread 第一轮不空流。
- **回滚**：新旧两条路径按端点独立开关。

### 第 3 步：下线进程内 Agent 与网关沙箱职责

- 删除网关对 `agent_sessions.get_session/initialize/create_config` 的依赖，以及 `bind_sandbox`/`bind_runtime`/`acquire_lease`/`release_lease`；
- 删除网关 lifespan 的 `cleanup_stale_warm`、`ensure_warm_pool`、`_sandbox_maintenance`、`destroy_warm_sandboxes`（`app/gateway/app.py:29-43,77-109`）；
- server 侧补 `runtime_context` 绑定；图工厂非执行上下文不认领沙箱；`username` 透传；
- 保留 `ChatTurn` 的展示消息持久化与 SSE 线格式（`chat_turn.py:92-100,108-122`、`sse.py:22-24`）。
- **独立验证**：网关启动后不再创建/维护任何容器；`docker ps` 的 warm/claimed 数 = 用户数 + 池大小；重启网关不删 server 的 warm；两用户各自沙箱与记忆；同 thread 并发第二个请求被拒。
- **回滚**：旧路径保留一个版本。

---

## 7. 参数基线（签名级，不含实现）

```python
stream = client.runs.stream(
    thread_id,
    "lead_agent",
    input={"messages": [{"role": "user", "content": message}]},
    # 或 command={"resume": ..., "update": {...}}，二者互斥
    config={"configurable": {"user_id": user_id, "username": username}},
    stream_mode=["messages-tuple", "values", "custom"],
    stream_subgraphs=True,
    if_not_exists="create",
    on_disconnect="cancel",
    multitask_strategy="reject",
    version="v1",
)

async for part in stream:
    mode, *ns = part.event.split("|")
    if mode in ("metadata", "end"):
        continue
    if mode == "error":
        # part.data: {"error": <类名>, "message": <文本>}
        ...
```

---

## 8. 关键引用索引

| 内容 | 文件:行号 |
|---|---|
| 现有流式调用与 `subgraphs=True` | `backend/app/services/chat_stream.py:104-109` |
| 现有事件分发 | `backend/app/services/chat_stream.py:110-128` |
| 现有 resume 构造 | `backend/app/services/chat_stream.py:34-59` |
| 现有 lease / runtime 绑定 | `backend/app/services/chat_stream.py:96-99,142-154` |
| `ChatTurn` values 处理 | `backend/app/services/chat_turn.py:125-195` |
| `ChatTurn` custom 处理 | `backend/app/services/chat_turn.py:197-223` |
| `ChatTurn` messages 处理 | `backend/app/services/chat_turn.py:225-306` |
| ToolMessage 处理 | `backend/app/services/chat_turn.py:308-363` |
| `source_label` | `backend/app/services/chat_turn.py:366-375` |
| 中断卡片整形 | `backend/app/services/hitl.py:107-129` |
| SSE 线格式 | `backend/app/services/sse.py:22-24` |
| `/state` 当前实现 | `backend/app/gateway/routers/chat.py:81-95,105-116` |
| 网关 lifespan 沙箱段 | `backend/app/gateway/app.py:29-43,77-109` |
| 图工厂 | `backend/packages/harness/erpflow/graph.py:28-57` |
| harness 沙箱维护 | `backend/packages/harness/erpflow/backends/pool_maintenance.py:29-52` |
| warm 清理 | `backend/packages/harness/erpflow/backends/sandbox_reaper.py:13-42` |
| lease 实现 | `backend/packages/harness/erpflow/backends/sandbox_manager.py:416-432` |
| runtime_context | `backend/packages/harness/erpflow/runtime_context.py:19-36` |
| review_router tag | `backend/packages/harness/erpflow/review/policy.py:142,157` |
| 现有中断测试 | `backend/tests/test_chat_interrupts.py:14-17` |
| SDK `StreamMode` | `SDK/schema.py:51-63` |
| SDK `StreamPart` | `SDK/schema.py:619-627` |
| SDK `runs.stream` 签名 | `SDK/_async/runs.py:203-235` |
| SDK command 序列化 | `SDK/_async/runs.py:326-330` |
| SDK `threads.get_state` | `SDK/_async/threads.py:486-496` |
| SDK `ThreadState` / `Interrupt` | `SDK/schema.py:295-303,327-358` |
| SDK namespace 换算 | `SDK/_shared/utilities.py:116-131` |
| SDK ServerRuntime 与 context | `SDK/runtime.py:91-163` |
| 服务端 mode 改写 | `API/stream.py:203-208` |
| 服务端 messages 事件与 ns | `API/stream.py:309-314` |
| 服务端 values/custom 事件与 ns | `API/stream.py:355-359` |
| 服务端子图中断拼接缺陷 | `API/stream.py:368-371` |
| 服务端序列化 | `API/serde.py:70-110,168-174` |
| 服务端 error 事件 | `API/sse.py:87-95` |
| 服务端 command 还原 | `API/command.py:8-31` |
| 工厂分派（config / runtime） | `API/_factory_utils.py:91-140` |
| 图工厂调用与 state 读取 | `API/graph.py:336-376` |
| thread 不存在 → 空流 | `langgraph_runtime_inmem/ops.py:2445-2499`（`_empty_generator()` 分支） |
| 消息 metadata tags | `langgraph/pregel/_messages.py:167-173` |
| rubric stream_writer | `deepagents/middleware/rubric.py:1336-1355` |
| DeerFlow 参考调用（反例） | `/home/snape/github_repo/deerflow2.0-enhanced/backend/app/channels/manager.py:566-577` |

W1A_DONE

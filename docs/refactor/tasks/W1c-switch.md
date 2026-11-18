# 任务卡 W1c — 网关切到 langgraph-sdk（带开关，默认不切）

**执行者**：codebuddy **前置**：W1b 已验收（适配层与 dict 兼容已就绪）
**依据**：`docs/refactor/reports/W1a-event-mapping.md` 第 2 节与第 3 节

## 目标

把「网关从进程内 `session.agent` 读流」变成「可选走 langgraph-sdk」，用环境变量切换，
**默认仍走进程内**，保证不改变现有行为。此卡只做切换，不删旧代码。

## 允许改动的路径（白名单）

- 新增 `backend/app/services/langgraph_client.py`（`get_client()` 懒加载单例）
- 修改 `backend/app/services/chat_stream.py`（按开关分派两条路径）
- 修改 `backend/app/gateway/routers/chat.py`（`/state` 按开关分派）
- 修改 `backend/app/gateway/config.py`（新增 `agent_backend` / `langgraph_url`）
- 新增 `backend/tests/test_chat_stream_sdk.py`

**禁止**：改 `chat_turn.py` / `sse.py` / `hitl.py` 的事件与字段；删 `agent_session.py`；
改 `erpflow/**`；改既有测试断言；新增 skip/xfail。

## 实现要求

### 1. 配置（`app/gateway/config.py`）

- `AGENT_BACKEND`：`inprocess`（默认）| `server`
- `LANGGRAPH_URL`：默认 `http://localhost:2024`

### 2. `langgraph_client.py`

- `get_client()`：首次调用 `langgraph_sdk.get_client(url=LANGGRAPH_URL)`，进程内复用
- 不开新的事件循环，不在 import 期建连接

### 3. `chat_stream.py`（server 模式）

```python
await client.threads.create(thread_id=thread_id, if_exists="do_nothing")   # 首次 run 前
async for part in client.runs.stream(
    thread_id, "lead_agent",
    input={"messages": [{"role": "user", "content": message}]},   # 仅新消息
    config={"configurable": {"thread_id": thread_id, "user_id": user_id, "username": username}},
    stream_mode=["messages-tuple", "values", "custom"],
    stream_subgraphs=True,
):
    parsed = adapt_stream_part(part.event, part.data)   # W1b 的适配层
    if parsed is None: continue
    ...喂给现有 ChatTurn 循环（事件生成逻辑一行不改）
```

- 恢复分支：`command={"resume": value, "update": {...}}`（`update` 仅在 decisions 非空时带上），
  且**只传 `command`、不传 `input`**
- 提前退出 `async for` 时调用 `await stream.aclose()`（或等价清理），避免服务端 run 悬挂
- 异常与服务端 `error` 事件统一走现有 `error` 事件壳（只取 `message`）
- `thinking` 的 `start`、`tool_end`、`done` 仍由网关合成

### 4. `/state`（server 模式）

- `state = await client.threads.get_state(thread_id)`；字段按 dict 访问
- 中断项用 `unwrap_interrupt` 解包后再找 `action_requests`

## 验收（主代理执行，全部必须过）

| 闸门 | 判定 |
|---|---|
| C1 | `uv run pytest tests/test_chat_stream_sdk.py -q` 全过（用 FakeClient，不连真服务） |
| C2 | 该测试断言：`stream_mode`/`stream_subgraphs` 参数正确；`config.configurable` 同时含 `user_id` 与 `username`；run 前调用过 `threads.create` |
| C3 | 同一 fixture 分别用 inprocess 与 server 两条路径跑，**事件名序列与关键字段完全一致**（逐条断言） |
| C4 | resume：只传 `command`、不带 `input`；`update` 仅在 decisions 非空时出现；`resume` 是 dict 不是 `Command` 对象 |
| C5 | `uv run pytest tests/ -q` 仍全绿（169 passed / 10 skipped 起） |
| C6 | `uv run ruff check .` 0 error |
| C7 | `git diff app/services/chat_turn.py app/services/sse.py` 为空 |
| C8 | `git status --short` 仅白名单内文件 |

## 产出

改动清单 + 两条路径事件序列的对比输出 + 未解决项。

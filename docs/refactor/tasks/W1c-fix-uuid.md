# 任务卡 W1c-fix — thread_id 必须映射为 UUID（实机验收发现的缺陷）

**执行者**：codebuddy **来源**：live smoke（`AGENT_BACKEND=server` 时 400）
**证据**：`POST /api/chat/stream` 返回
`event: error / data: {"message": "服务内部错误: Invalid thread ID: must be a UUID"}`

## 缺陷

LangGraph Server 要求 thread id 是 UUID；本项目的 `thread_id` 是任意字符串
（`ChatRequest.thread_id`，如 `smoke-t1`），直接透传会导致所有会话首轮失败。

## 修复要求

- 新增映射函数（放在 `app/services/langgraph_client.py` 或新的 `app/services/thread_ids.py`）：

```python
def server_thread_id(thread_id: str) -> str:
    """应用侧任意字符串 → 稳定的服务端 UUID（同输入永远同输出）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"erp-agent:{thread_id}"))
```

- **所有** SDK 调用（`threads.create`、`runs.stream`、`threads.get_state`）使用映射后的 id
- **所有**对外行为保持不变：SSE 里不带 thread_id、Mongo `conversations`/`display_messages`、
  `/api/chat/{thread_id}/state`、`/history` 仍用原始 `thread_id`
- 映射必须确定性（进程重启后同一会话仍命中同一 thread），不能用 `uuid4`

## 白名单

`app/services/langgraph_client.py`（或新增 `app/services/thread_ids.py`）、
`app/services/chat_stream.py`、`app/gateway/routers/chat.py`、`tests/test_chat_stream_sdk.py`

禁止改 `chat_turn.py` / `sse.py` / `erpflow/**` / 其它测试断言。

## 验收

| 闸门 | 判定 |
|---|---|
| D1 | `uv run pytest tests/ -q` 全绿（≥176 passed） |
| D2 | 新测试：同输入同输出、不同输入不同、结果是合法 UUID、`uuid.uuid4` 未被使用 |
| D3 | 新测试：`runs.stream` / `threads.create` / `get_state` 收到的都是映射后的 id，而 API 响应仍用原始 id |
| D4 | `uv run ruff check .` 0 error |
| D5 | 主代理实机复测：`AGENT_BACKEND=server` 下 `POST /api/chat/stream` 能拿到 `token` 与 `done`，且 `/state` 可用 |

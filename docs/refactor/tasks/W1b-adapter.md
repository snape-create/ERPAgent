# 任务卡 W1b — LangGraph SDK 事件适配层（不切换生产流量）

**执行者**：codebuddy（可跑 `uv run pytest` / `uv run ruff`） **前置**：W1a 已验收
**依据**：`docs/refactor/reports/W1a-event-mapping.md`（本卡不重复其结论，只落地第 1 步）

## 目标

新增一层「SDK `StreamPart` → 现有 `ChatTurn` 输入」的适配，并让 `ChatTurn` 同时兼容
**LangChain 对象**与 **SDK 序列化后的 dict**。此步**不改变**网关现在的运行方式
（仍走进程内 `session.agent.astream`），只把适配能力与测试准备好。

## 允许改动的路径（白名单）

- 新增 `backend/app/services/langgraph_adapter.py`
- 修改 `backend/app/services/chat_turn.py`（仅做 dict 兼容与中断值解包，不改事件语义）
- 新增 `backend/tests/test_langgraph_adapter.py`
- 需要时可动 `backend/app/services/hitl.py`（仅中断值解包）

**禁止**：改 `chat_stream.py`、`chat.py`、`agent_session.py`、`erpflow/**`、`sse.py` 的事件名集合；
不得新增 skip/xfail；不得改既有测试断言。

## 实现要求

### 1. `langgraph_adapter.py`

```python
def adapt_stream_part(event: str, data):  # -> (namespace: list[str], mode: str, payload) | None
```
- `mode = event.split("|")[0]`，`namespace = event.split("|")[1:]`（主图为 `[]`）
- `mode in {"metadata", "end", "error"}` → 返回 `None`（error 由 `chat_stream` 现有分支统一封装）
- `values` / `custom`：`payload` 原样返回
- `messages` / `messages-tuple`：`payload` 保持 `[message_dict_or_obj, metadata]` 形状

```python
def message_field(message, key, default=None)   # 同时支持 BaseMessage 与 dict
def unwrap_interrupt(item)                      # {"value": x, "id": y} → x；否则原样返回
```

### 2. `chat_turn.py` 兼容性改造

- 所有 `getattr(msg, "type"/"content"/"tool_call_chunks"/"name"/"tool_call_id", ...)`
  改为 `message_field(...)`；`tool_call_chunks` 里的每个 chunk 也要兼容 dict
  （`chunk.get("name")` / `chunk.get("args")`）
- `values` 里的中断项先经 `unwrap_interrupt()` 再交给 `build_interrupt_event`
- 事件名、顺序、字段名**一律不变**（前端契约）

## 验收（主代理执行，全部必须过）

| 闸门 | 命令/判定 |
|---|---|
| B1 | `uv run pytest tests/test_langgraph_adapter.py -q` 全过 |
| B2 | 新测试覆盖：主图/子图 namespace；`values`/`custom` 透传；`metadata`/`end` 被忽略；dict 消息产出 `token`+`tool_start`/`tool_args`/`tool_result`/`tool_end`；子图文本 `source` 非 `main`；`{"value","id"}` 中断能还原审批卡片；`review_router` tags 被过滤 |
| B3 | `uv run pytest tests/ -q` 仍为 157 passed / 10 skipped（只增不减，新增用例除外） |
| B4 | `uv run ruff check .` 0 error |
| B5 | `git diff app/services/sse.py` 为空（事件名集合不变） |
| B6 | `git status --short` 仅白名单内文件 |

## 产出格式

改动文件清单 + 测试输出尾部 + 未解决项。发现问题写入报告，不要顺手改白名单外的文件。

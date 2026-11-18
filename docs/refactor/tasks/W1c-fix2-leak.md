# 任务卡 W1c-fix2 — 修复 review_router 内部调用泄露为 tool_args

**执行者**：codebuddy **来源**：`docs/refactor/reports/W1-live-smoke.md` 第 3 轮
**判定**：W1 实机验收唯一的阻塞缺陷

## 现象

`AGENT_BACKEND=server` 时，`/api/chat/stream` 输出 23 条 `tool_args`，
且没有任何 `tool_start` / `tool_result` 与之配对。直连 SDK 证实这些片段来自
评审路由器的结构化输出（`tool_call_chunks[0].name == "ReviewRoute"`，后续块 `name` 为 `None`）。

元数据里 `tags == ["review_router"]` 是存在的，但过滤没有拦住后续片段。

## 要求

1. 在 `ChatTurn` 内**按 tool_call id 维护"被过滤"集合**：
   - 某条消息（或某个 tool_call chunk 组）一旦因 `metadata.tags` 含 `review_router` 被判为内部调用，
     其后续增量（`name` 为空、只有 `args` 的块）也必须一并丢弃，不得产生 `tool_args`
   - 由于 SDK 序列化后 `id` 可能只在首块出现，允许用"消息序号/同一 metadata 对象"作为退化键，
     但必须在测试里覆盖 `id` 缺失的情况
2. 同样的规则适用于 `token`（内部思考文本不得作为正文）与 `tool_result`
3. **事件名、顺序、字段名不得改变**；正常业务工具的 `tool_start → tool_args → tool_result → tool_end` 必须保持原样

## 白名单

- `backend/app/services/chat_turn.py`
- `backend/tests/test_langgraph_adapter.py`（或新增 `backend/tests/test_tool_leak_filter.py`）
- 必要时 `backend/app/services/langgraph_adapter.py`

禁止改 `chat_stream.py` / `chat.py` / `sse.py` / `erpflow/**`；不得新增 skip/xfail。

## 验收

| 闸门 | 判定 |
|---|---|
| E1 | 新增测试：首块带 `tags=["review_router"]` + `name`，后续块 `name=None` 只有 `args` → **零** `tool_args` / `token` 事件 |
| E2 | 新增测试：正常业务工具（无 review_router 标签）→ `tool_start` / `tool_args` / `tool_result` / `tool_end` 齐全且顺序不变 |
| E3 | 新增测试：同一轮里既有评审调用又有业务工具调用，二者互不干扰 |
| E4 | `uv run pytest tests/ -q` 全绿（≥181 passed） |
| E5 | `uv run ruff check .` 0 error |
| E6 | 主代理实机复测：同一 prompt 的事件序列为 `thinking → phase → thinking(end) → token… → done`，`tool_args` 不复现 |

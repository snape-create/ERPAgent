# 任务卡 W1a — 事件映射设计（只读）

**执行者**：pi（只读） **前置**：无 **产出**：结论报告（写入 `docs/refactor/reports/W1a-event-mapping.md`）

## 目标

为「网关从进程内装配 Agent 切到 `langgraph-sdk` 客户端」提供**事件映射表**与**风险清单**，供 W1 实施任务卡使用。

## 输入（只读这些文件）

| 文件 | 关注点 |
|---|---|
| `backend/app/services/chat_stream.py` | 现在用 `session.agent.astream(stream_mode=["messages","values","custom"], subgraphs=True)`，产出三元组 `(namespace, chunk_type, chunk)` |
| `backend/app/services/chat_turn.py` | 消费三元组生成 SSE 事件（含 `tool_call_chunks`、`metadata.tags` 过滤、namespace→source 标注） |
| `backend/app/services/hitl.py` | 中断值 ↔ 审批卡片；`Command(resume=..., update={"_human_approved": ...})` |
| `backend/app/services/sse.py` | SSE 线格式与事件名集合 |
| `backend/app/gateway/routers/chat.py` | `/state` 端点用 `session.agent.aget_state` |
| `deerflow2.0-enhanced/backend/app/channels/manager.py` | 参考：`client.runs.stream(thread_id, assistant_id, input=..., config=..., context=..., stream_mode=["messages-tuple","values"])` |

## 必须回答的 5 个问题

1. `langgraph_sdk` 的 `runs.stream` 支持哪些 `stream_mode`；返回的 `StreamPart` 结构（`event` / `data`）；`subgraphs=True` 时 namespace 从哪里取。
2. 逐条映射表：现有 SSE 事件 `thinking / token / tool_start / tool_args / tool_result / tool_end / phase / todo_update / review_result / interrupt / done / error` ↔ SDK 事件来源；标出**无法映射**或**语义有差异**的项。
3. 中断恢复：现有 `Command(resume=..., update={"_human_approved": ...})` 改走 SDK 的写法（`runs.stream` 的 `command` 参数）；`/state` 端点对应的 `threads.get_state` 调用形态。
4. 风险清单，至少覆盖：
   - `tool_call_chunks` 在 SDK 流里是否还拿得到
   - `messages` 流的 `metadata.tags`（用于过滤 `review_router`）是否保留
   - `custom` 事件是否透传
   - per-user 上下文（`user_id` / `username`）如何传给 server（`context=` vs `config.configurable`）
   - 沙箱预热池由谁维护（现在 `erpflow.backends.pool_maintenance` 与网关 lifespan 各一份）
   - `display_messages` 的节流持久化是否仍能在网关侧完成
5. 「最小改动切换路径」的三步建议（每步可独立验证）。

## 硬约束

- **只读**：不得修改仓库内任何文件（除本任务卡的产出报告）。
- 不给完整实现代码，只给结论、API 签名、映射表。
- 结论要可核对：引用文件:行号。

## 验收（主代理执行）

| 闸门 | 判定 |
|---|---|
| A1 | 5 个问题都有明确答案，无「可能/大概」式表述结论 |
| A2 | 映射表覆盖全部 12 个现有事件，差异项标注清楚 |
| A3 | 风险清单 ≥6 条且每条给出影响面 |
| A4 | 无代码改动（`git status` 只多出报告文件） |

# 任务卡 W1c-fix3 — 内部结构化调用仍泄露（OrderIntentDecision）

**执行者**：codebuddy **来源**：`docs/refactor/reports/W1-live-smoke.md` + 第 4 轮复测
**判定**：W1 实机验收仍不通过

## 复测证据（`AGENT_BACKEND=server`，同一 prompt）

```
事件序列: thinking ×2 → phase(result) → thinking(end) → tool_start ×1 → tool_args ×22 → token ×33 → done
tool_start 数据: {"name": "OrderIntentDecision", "id": "call_00_q0TzUjSVJ916EYJHkBVr5795"}
tool_args 样本:   {"args":"{\""} {"args":"create"} {"args":"_order"} {"args":"\""} {"args":": "}
tool_result: 0    interrupt: 0    done: {"interrupted": false}
```

即：**OrderIntakeMiddleware 的意图判定调用被当成业务工具**，前端会画出无主卡片。

## 现状（供参考，不要只改这里）

`chat_turn.py:35` 已有 `INTERNAL_TOOL_PATTERN = (grade|rubric|order_?intent|review_?route)`，
`chat_turn.py:270` 用 `call_name` 判断抑制，但本次仍发出 `tool_start`，说明：

- 名称可能只在**后续片段**出现（首片段 `name` 为空，args 先到），首片段未被抑制；
- 或抑制集合按 `id` 维护，而 `id` 只在首片段出现，导致后续片段匹配不到。

## 要求

1. 抑制判定基于**名称 + 标签 + 已判定集合**三重：
   - 名称一旦出现（无论第几个片段），命中 `INTERNAL_TOOL_PATTERN` → 该调用**整体**丢弃，
     并且要**回溯丢弃**此前已缓冲/已发出的 `tool_args`（实现上建议先缓冲再发，或按 id 分组）
   - `tags` 含 `review_router` 的消息同样整体丢弃（含 `token`）
2. 正常的业务工具（如 `create_order`、`get_inventory`）必须完整保留
   `tool_start → tool_args → tool_result → tool_end`
3. 事件名/顺序/字段名不变；不得引入延迟发送导致正常工具事件缺 `tool_start`

## 白名单

`backend/app/services/chat_turn.py`、`backend/tests/test_tool_leak_filter.py`（可新建）、
必要时 `backend/app/services/langgraph_adapter.py`

禁止改 `chat_stream.py` / `chat.py` / `sse.py` / `erpflow/**`；不得新增 skip/xfail。

## 验收

| 闸门 | 判定 |
|---|---|
| F1 | 新测试：名称在第 3 个片段才出现时，前 2 个片段的 `tool_args` 也不得输出 |
| F2 | 新测试：`OrderIntentDecision` / `ReviewRoute` / `grade_rubric` 三种名称全部被抑制 |
| F3 | 新测试：业务工具仍产出 4 类事件且顺序正确 |
| F4 | `uv run pytest tests/ -q` 全绿（≥185 passed） |
| F5 | `uv run ruff check .` 0 error |
| F6 | 主代理实机复测：同一 prompt 无 `tool_start` / `tool_args`（除非真的是业务工具），`token` 与 `done` 正常 |

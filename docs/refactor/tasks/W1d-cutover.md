# 任务卡 W1d — 切默认到 server 并删除网关内 Agent 生命周期

**执行者**：codebuddy **前置**：W1c / fix / fix2 / fix3 已验收（含实机）
**依据**：`docs/refactor/reports/W1-live-smoke.md`（第 5 轮：happy path + 两种中断 + resume + review_result 全部实测通过）

## 目标

把 `AGENT_BACKEND` 默认值改为 `server`，并删除网关侧的进程内 Agent 与沙箱职责。
完成后网关只做 HTTP + SSE + 持久化，Agent 只活在 LangGraph Server 进程。

## 允许改动的路径（白名单）

- 删除 `backend/app/services/agent_session.py`
- `backend/app/gateway/app.py`：删掉 lifespan 里的沙箱补池/维护线程/销毁、`agent_sessions.initialize`
- `backend/app/gateway/routers/chat.py`：删掉 `agent_sessions.*` 调用
- `backend/app/services/chat_stream.py`：删掉 inprocess 分支与租约逻辑，只保留 SDK 路径
- `backend/app/gateway/config.py`：默认 `agent_backend = "server"`
- `backend/tests/test_chat_interrupts.py`：改为走 SDK 路径（用 FakeClient）
- `backend/tests/test_chat_stream_sdk.py`：补齐原 inprocess 用例覆盖的场景

**禁止**：改 `chat_turn.py` 的事件语义、`sse.py`、`hitl.py`、`erpflow/**`、其它测试断言；
不得新增 skip/xfail；不得保留死代码或“兼容分支”。

## 要求

1. 网关进程启动后**不再创建/维护任何沙箱容器**，也不再构建 Agent
2. `ChatTurn` 的展示消息持久化（节流 + `shield` 兜底）与 `/history` 保持不变
3. `resume`、`/state`、`error`、`done` 行为与 W1c 验收时一致
4. 删除后 `app/` 下不得再出现 `agent_sessions`、`sandbox_manager`、`acquire_lease`、`bind_sandbox` 的引用

## 验收

| 闸门 | 判定 |
|---|---|
| H1 | `grep -rn "agent_sessions\|sandbox_manager\|acquire_lease" backend/app` 无命中 |
| H2 | `uv run pytest tests/ -q` 全绿（≥192 passed，skip 数量不增加） |
| H3 | `uv run ruff check .` 0 error |
| H4 | `git status` 仅白名单文件变化；`agent_session.py` 已删除 |
| H5 | 主代理实机：起 server + 网关（默认配置、不设 `AGENT_BACKEND`）跑一轮对话 + 一轮中断/审批，事件与 W1c 一致；网关进程不再出现 “Warm pool refill” 日志 |

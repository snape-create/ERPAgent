# W1 实机验收记录（AGENT_BACKEND=server）

环境：Mongo ✅ / Docker ✅ / `uv run langgraph dev --allow-blocking --port 2024` / gateway `:8010`

## 第 1 轮：UUID 缺失（已修）

```
POST /api/chat/stream  {"message":"你好，用一句话自我介绍","thread_id":"smoke-t1"}
→ event: error  data: {"message":"服务内部错误: Invalid thread ID: must be a UUID"}
```

根因：LangGraph Server 只接受 UUID thread id，网关把任意字符串直接透传。
修复（W1c-fix）：`server_thread_id()` = `uuid5(NAMESPACE_URL, f"erp-agent:{thread_id}")`，SDK 侧统一映射，对外仍用原始 id。

## 第 2 轮：blocking I/O（dev 参数问题）

```
blockbuster.blockbuster.BlockingError: Blocking call to os.getcwd
docker.errors.DockerException: Blocking call to socket.socket.connect
```

根因：graph 工厂（`erpflow.graph.make_lead_agent` → `create_main_agent`）在事件循环里做
Docker 连接、`os.getcwd()`、MCP 子进程启动等阻塞调用，`langgraph dev` 默认用 blockbuster 拦截。
处置：dev 启动加 `--allow-blocking`（参考项目同样如此）。
**遗留**：生产环境（LangSmith Deployment）不允许阻塞，需把沙箱/工具初始化移出工厂（记入 ROADMAP 已知限制）。

## 第 3 轮：事件泄露（未修，阻塞验收）

```
事件序列: thinking ×2 → phase(result) → thinking(end) → tool_args ×23 → token ×32 → done
tool_start: 0   tool_result: 0
tool_args 样本: {"args":"{\""} / {"args":"create"} / {"args":"_order"}
```

结论：
1. `token` 正常（`{"content":"我是","source":"main"}` 等 32 条），`done` 正常。
2. **`tool_args` 泄露 23 条，且没有配对的 `tool_start` / `tool_result`** —— 前端会渲染出无主工具卡片。
3. 直连 SDK 探测证实这些片段来自模型的结构化输出流：
   ```
   messages | {'tool_call_chunks': "[{'name': 'ReviewRoute', 'args': '', ...}]"}
   messages | {'tool_call_chunks': "[{'name': None, 'args': '{\"', ...}]"}
   ```
   即**评审路由器（review_router）的内部调用被当成业务工具事件**。
4. 元数据里确实带 tags：
   ```
   meta keys: [... 'langgraph_node', 'langgraph_path', ...] | tags= ['review_router']
   ```
   说明 `ChatTurn` 的 `metadata.get("tags")` 过滤在此路径下**未生效**（首块带 name 的被丢弃后，
   后续 `name=None` 的 `args` 片段仍被累积并发出 `tool_args`）。

## 验收判定

| 闸门 | 结果 |
|---|---|
| 离线（pytest 181 passed / ruff clean） | 通过 |
| D5 实机：能拿到 token / done | 通过 |
| 事件与现有契约一致（无多余事件） | **不通过**：`tool_args` 泄露 |

# 任务卡 W5 — graph 工厂去阻塞（不再需要 `--allow-blocking`）

**执行者**：codebuddy（可用 `-y` 跑测试） **来源**：`docs/refactor/reports/W1-live-smoke.md` 第 2 轮
**唯一验收标准**：**不带** `--allow-blocking` 也能跑通 5 轮实机

## 缺陷

`erpflow.graph.make_lead_agent`（`graph.py:47`）每次 run 被调用，内部 `create_main_agent`
在事件循环线程里做阻塞调用，被 LangGraph 的 blockbuster 拦截：

```
blockbuster.blockbuster.BlockingError: Blocking call to os.getcwd
docker.errors.DockerException: ... Blocking call to socket.socket.connect
```

阻塞点（`backend/packages/harness/erpflow/agent/build.py`）：

| 行号 | 调用 | 阻塞原因 |
|---|---|---|
| 120 | `sandbox_manager.get_sandbox()` | Docker daemon socket |
| 122 / 245 | `bootstrap_sandbox()` | tar 打包 + 上传 + 解包（94 文件） |
| 135 | `LocalShellBackend(virtual_mode=True)` | `os.getcwd()` |
| 159-163 | `load_mcp_tools_sync()` | 起子进程 + 连 3 个外部 MCP |
| — | `get_store()`（`graph.py:53`） | pymongo 连接 + 建索引 |

## 方案（两层，先做第一层就够本卡验收）

### 第 1 层（必须）：阻塞调用移出事件循环 + 每用户图缓存

- 新增 `erpflow/agent/graph_cache.py`：
  - 模块级 `ThreadPoolExecutor(max_workers=4, thread_name_prefix="graph-build")`
  - `_graphs: dict[str, CompiledStateGraph]` + `Lock`
  - `get_or_build(user_context) -> graph`：命中直接返回；未命中提交到 executor 并 `future.result(timeout=180)`
- `graph.py.make_lead_agent` 改为调用 `graph_cache.get_or_build(...)`，函数体里**不得**再直接调用
  Docker / 磁盘 / pymongo / MCP 相关函数
- 进程预热（同一个 executor，`_warmup_done` 保证只跑一次）：
  - `load_mcp_tools_sync()`（`mcp_client` 已有 `_cached_tools`，预热后每次建图不再连外部 MCP）
  - `get_store()`
  - `ensure_sandbox_pool_once()`
- 预热失败只告警，不阻塞首个请求（回退为首次建图时在线程里做）

### 第 2 层（记录，本卡不做）

首次请求仍需在线程里等 Docker；彻底方案是 `SandboxBackendProxy` 延迟解析 + `before_agent` 里
`asyncio.to_thread`。本卡只在报告里写明，作为 W6 待办。

## 允许改动的路径（白名单）

- 新增 `backend/packages/harness/erpflow/agent/graph_cache.py`
- `backend/packages/harness/erpflow/graph.py`
- `backend/packages/harness/erpflow/agent/build.py`（仅缓存/线程相关最小改动）
- `backend/packages/harness/erpflow/tools/mcp_client.py`（如需导出预热入口）
- 新增 `backend/tests/test_graph_factory.py`

**禁止**：改 `app/**`、`chat_turn.py`、`sse.py`、事件契约；不得新增 skip/xfail；
不得在工厂里用 `run_until_complete` 之类嵌套事件循环的写法。

## 验收

| 闸门 | 判定 |
|---|---|
| K1 | `uv run pytest tests/ -q` 全绿（≥192 passed，skip 不增加） |
| K2 | `uv run ruff check .` 0 error |
| K3 | 新测试：同 user 连续两次 `make_lead_agent` 返回**同一对象**（缓存生效）；不同 user 不同对象 |
| K4 | 新测试：工厂执行期间不直接调用 `docker` / `os.getcwd`（用 monkeypatch 断言这些入口未被调用，或断言其只在 executor 里被调用） |
| K5 | 主代理实机：`uv run langgraph dev --no-browser --no-reload`（**无** `--allow-blocking`）启动成功，日志无 `BlockingError` |
| K6 | 主代理实机：纯对话 / 补充信息中断 / 二次 resume / 审批卡片 / approve 后 `tool_result+review_result+done` 五轮全过 |
| K7 | 主代理实机：同一 user 第二次对话明显更快（无 MCP 重连、无容器重传日志） |

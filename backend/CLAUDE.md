# ERP Agent 后端：架构与开发约定

面向要改代码的人（以及 AI 助手）。只想跑起来看 [../README.md](../README.md)。

## 1. 两层结构（硬约束）

```
backend/
├── packages/harness/erpflow/   # 核心层：import 前缀 erpflow.*，独立安装包 erpflow-harness
└── app/                        # 网关层：import 前缀 app.*，不打包，靠 backend/ 作为 cwd 运行
```

**依赖方向单向**：

```
app  →  erpflow     允许
erpflow  →  app     禁止
```

- 执行方式：`tests/test_harness_boundary.py` 用 AST 静态扫描 `packages/harness/erpflow/**.py`，
  出现 `app.` / `erp_mock.` 的 import 即失败。
- 判定口诀：**能换一个 ERP 系统继续跑的代码放 harness；只对"这个 Web 应用"有意义的代码放 app。**
- 因此 `erpflow` 里不允许出现：FastAPI 类型、HTTPException、Mongo 会话 CRUD、SSE 线格式。

`erp_mock/` 是**外部 ERP 的模拟器**，不属于上面任何一层，刻意不与 `erpflow` 共享代码
（否则就不是"外部系统"了）；它只用 FastAPI + SQLAlchemy 复刻 23 个业务接口。

## 2. 运行面

| 进程 | 命令（都在 `backend/` 下） | 端口 |
|---|---|---|
| Agent 服务 | `uv run langgraph dev --no-browser --no-reload` | 2024 |
| 网关 | `uv run python -m app.gateway.app` | 8000 |
| Mock ERP | `uv run python -m erp_mock.main` | 8081 |
| MCP Server（可选） | `uv run python -m app.mcp_server.main` | 9000 |

- `langgraph.json` 声明图入口 `erpflow.graph:make_lead_agent` 与 checkpointer
  `erpflow/checkpointer.py:make_checkpointer`。
- `checkpointer.py` 是被**按文件路径加载**的，所以那里必须用绝对 import（`from erpflow.x import y`），
  相对 import 会报 `attempted import with no known parent package`。
- 沙箱预热池跟着 Agent 所在进程走：由 `erpflow.backends.pool_maintenance` 维护，
  网关不持有容器。

## 3. 目录职责

### harness（`packages/harness/erpflow/`）

| 目录/文件 | 职责 |
|---|---|
| `graph.py` | LangGraph Server 图工厂（`make_lead_agent(config)`） |
| `checkpointer.py` | checkpointer 工厂：Mongo 优先，失败回退 InMemorySaver |
| `agent/` | `build.py` Agent 装配（LLM + 沙箱 + 工具 + 子 Agent + 中间件 + 提示词）；`bootstrap.py` 沙箱引导（只传 `skills/` + `erpflow/`） |
| `skills/` | 技能加载与渐进披露（`progressive.py`）；技能数据在 `backend/skills/` |
| `middlewares/` | 每个关注点一个中间件 |
| `memory/` | 三层记忆（HOT=checkpoint / WARM / COLD） |
| `review/` | 阶段状态机（phase）+ 路由策略（policy）+ 决策模型适配（jev）+ `config.yaml` |
| `backends/` | 沙箱：`sandbox_manager`(状态机) `sandbox_entry`(条目+建容器) `sandbox_cache`(Mongo 映射) `sandbox_reaper`(孤儿清理) `pool_maintenance`(补池) |
| `tools/` | 自定义工具 + 外部 MCP 客户端 |
| `integrations/erp/` | ERP HTTP 客户端、状态字典、审批事实查询、23 个领域工具（唯一实现） |
| `subagents/` | YAML 声明式子 Agent |
| `store.py` | `MongoDBStore` + 进程级单例 `get_store()` |
| `config/` | 配置单一入口：`paths.py`（路径 + .env）、`sandbox.py`（TOML > env > 默认）、`settings.py`（env → pydantic 单例）、`llm.py`（get_llm） |

### 网关（`app/`）

| 目录 | 职责 |
|---|---|
| `gateway/app.py` | 唯一装配点：`create_app()` + lifespan + `/health` |
| `gateway/config.py` | 进程参数（env → pydantic → 单例） |
| `gateway/routers/` | 一文件一 `router`，只做 HTTP 语义 |
| `services/` | 编排：`chat_stream`(会话/锁/租约) `chat_turn`(chunk→SSE) `hitl`(中断↔卡片) `sse`(线格式) `agent_session`(Agent 生命周期) |
| `repositories/` | 数据访问：`mongo`(连接) `conversations`(会话/消息/追踪) |
| `mcp_server/` | 独立 MCP 进程的薄启动器 |

## 4. 约定

1. **配置**：进程/部署参数走 `app.gateway.config`（只读 env）；业务参数走 `erpflow.config.get_settings()`。
   沙箱是唯一例外（`config/sandbox_config.toml` 覆盖环境变量），由 `erpflow.config.sandbox` 统一实现。
   **禁止**在其它模块新增 `os.getenv`，也不要在业务代码里自己拼 `Path(__file__).parent`。
   优先级：显式环境变量 > `.env`（`load_env(override=False)`）> 代码默认值。
2. **单例**：只允许 `agent_sessions`、`sandbox_manager`、`sandbox_cache`、`erpflow.store._store` 这类
   "进程级资源"，且必须提供测试可重置的入口。
3. **持久化**：会话元数据/展示消息/trace 一律走 `app/repositories/conversations`；harness 只碰自己的集合
   （`langgraph_store`、`sandbox_cache`）。
4. **SSE 契约**：前端只认 `event: <name>\ndata: <json>`；新增事件必须同时更新
   `app/services/sse.py` 与 `docs/ARCHITECTURE.md` 的事件列表。
5. **测试**：扁平 `tests/`，文件名即分层标签（`test_harness_boundary` = 架构约束、
   `test_<x>_router` = 网关、`test_<feature>` = 常规）；不写 `sys.path` 补丁，路径统一在 `conftest.py`。
6. **文档同步**：改结构/命令时同步 `README.md` 与本文件。
7. **测试**：`uv run pytest tests/` 应当全绿。需要真实 ERP / LLM / Docker / MCP Server 的脚本
   （`test_agent`、`test_all_tools`、`test_mcp`、`test_business_eval`）在文件顶部用
   `pytestmark = pytest.mark.skip(...)` 标注，手动验证时加 `--no-skip` 不生效——
   直接单独 `pytest <file>` 也不会跑，请用 `evals/` 或注释掉标记后运行。
8. **追踪**：`LANGSMITH_TRACING` 默认关闭（settings 里显式建模）；测试由 `conftest.py` 强制关掉。

## 5. 已知问题

- `langgraph-api 0.7.103` 已 EOL（官方建议 0.18.x）。当前与参考项目保持一致钉在 `>=0.7,<0.8`，
  升级需单独验证运行时 API 变化。
- 沙箱镜像已升到 `python:3.13-slim`，需重新 `docker compose build sandbox` 才生效。

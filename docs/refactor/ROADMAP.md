# 后端重构路线（ROADMAP）

> 角色约定：**审计/验收/记录**由主代理负责；**实现**交给 `codebuddy` 与 `pi`（终端 CLI）。
> 验收记录见 [ACCEPTANCE.md](./ACCEPTANCE.md)。

## 0. 目标与不变量

参照 `deerflow2.0-enhanced/backend`，把后端拆成两层并保持单向依赖：

```
app（网关，不打包）  →  erpflow（harness，可安装）  →  integrations/erp
                       erpflow 绝不能 import app / erp_mock
```

以下五条是**不变量**，任何变更都不得破坏：

| # | 不变量 | 守护方式 |
|---|---|---|
| 1 | `erpflow` 不 import `app` / `erp_mock` | `tests/test_harness_boundary.py`（AST 扫描） |
| 2 | 配置只有一个入口 | `erpflow/config/`；其它模块不得新增 `os.getenv`（三处例外已注明） |
| 3 | 路径只有一个来源 | `erpflow.config.paths`；禁止再写 `parents[n]` |
| 4 | 前端 SSE 契约不变 | `event: <name>\ndata: <json>`，事件名见 `app/services/sse.py` |
| 5 | 手动验收脚本默认 skip | `test_agent` / `test_all_tools` / `test_mcp` / `test_business_eval` |

## 1. 已完成（含证据）

| 阶段 | 内容 | 关键数字 |
|---|---|---|
| P0 骨架 | `src/` → `backend/{app,packages/harness/erpflow,erp_mock,skills,tests,config}`，import 前缀统一 | 5 处 `sys.path.insert` 清零 |
| P1 配置 | `erpflow/config/`（paths/sandbox/settings/llm），`os.getenv` 收口，`.env` 改 `override=False` | `ruff` 0 error；配置测试 42 项 |
| P2 网关拆分 | `chat.py` 拆为 routers + services + repositories | 762 → 116 行 |
| P3 harness 内聚 | 沙箱拆 5 模块、删死代码、store/checkpointer/graph 就位 | `sandbox_manager` 653 → 467 |
| P4 测试/文档 | pytest 化、conftest 统一路径、手动脚本 skip、README/CLAUDE 同步 | 157 passed / 10 skipped |
| 运行时分层（半） | `langgraph.json` + `graph.py` + `checkpointer.py` + store 下沉 | `uv run langgraph dev` → `/ok`，`lead_agent` 注册；建图 27 nodes |
| 目录收敛 | 顶层 13 → 7 个文件：`agent/` `review/` `skills/` 三个新包；删 `env_utils`；tar 收窄 | 上传文件 ~300+ → 94 |

## 2. 剩余任务

> 执行者：`codebuddy` = 实现；`pi` = 独立审计/设计；主代理 = 出任务卡 + 验收。

| ID | 任务 | 优先级 | 预估 | 执行者 | 依赖 |
|---|---|---|---|---|---|
| ~~**W1**~~ | ~~网关切 `langgraph-sdk`~~ | — | — | **已完成（W1a→W1d）** | — |
| ~~W1a~~ | ~~事件映射表~~ | — | — | 完成（pi 两次无输出，改派 codebuddy） | — |
| ~~W1b~~ | ~~SDK 事件适配层 + dict 兼容~~ | — | — | 完成 | — |
| ~~W1c~~ | ~~网关双路径 + 开关~~ | — | — | 完成（含 UUID 映射与两轮泄露修复） | — |
| ~~W1d~~ | ~~切默认到 server、删网关内 Agent 与沙箱职责~~ | — | — | 完成 | — |
| **W2** | CI：`uv sync` + `ruff` + `pytest`（含边界测试） | 中 | 30min | codebuddy | 无 |
| **W3** | 升级 `langgraph-api` 0.7 → 0.18（调查已完成，报告在 `reports/W3-*.md`） | 中 | 2~4h | 待排期（结论需按 server 为默认重估） | W1 |
| **W4** | 文档/环境小卫生：`.env` 死键、`docs/ARCHITECTURE.md` 端点表补 `/api/mcp/*` | 低 | 20min | codebuddy | 无 |

W1 完成后可删除：`app/services/agent_session.py`、网关 lifespan 中的沙箱段、`app/repositories/mongo.py` 的同步 Mongo 用法（若不再需要）。

## 3. 已知遗留与风险

| 项 | 说明 |
|---|---|
| 沙箱镜像 | `docker/Dockerfile.sandbox` 已改 `python:3.13-slim`，**需重新 build** |
| `ruff format` | 从未执行；会产生 ~90 文件纯空白 diff，建议单独一次 |
| 外部 MCP 冷启动 | `backend/config/mcp.json` 的 bing/amap/12306 连不上时建图 7.9s → 53.8s，工具 59 → 51；不用建议移除 |
| `langgraph-api` | 0.7.103 已 EOL，钉 `>=0.7,<0.8` 与参考项目一致 |
| 工作区状态 | 159 项变更**未提交**；`git log` 最后一次提交仍是重构前 |

## 4. 委派约定（给 codebuddy / pi 的硬约束）

任务卡必须包含：目标、允许改动的路径白名单、禁止事项、验收命令。

**禁止事项（通用）**：

1. 不得修改测试断言来让测试通过；不得新增 `skip` / `xfail` 掩盖失败。
2. 不得改 `.env`（用户私有文件）；只能改 `.env.example`。
3. 不得在 `/tmp` 或隐藏目录创建文件；临时产物放仓库内可见路径并说明。
4. 不得删除既有测试文件；不得改动 `tests/test_harness_boundary.py` 的规则。
5. 越界发现的问题写入报告，不要顺手修（由主代理排期）。

**产出格式**：改动文件清单 + 关键 diff 摘要 + `uv run ruff check .` 与 `uv run pytest tests/ -q` 的原始输出尾部 + 未解决项。

# 架构与实现说明

面向要改代码的人。只想跑起来的话看 [README](../README.md)。

## 1. 系统结构

```
Frontend (Next.js :3000)
  │  SSE：thinking / token / tool_start / tool_result / phase / todo_update / interrupt / done
Backend API (FastAPI :8000)
  │  chat.py（流式对话、中断恢复、展示消息持久化）+ history.py（会话 CRUD）
  │  MongoDBSaver（checkpoint）+ MongoDBStore（跨会话记忆）
Agent Core (DeepAgent + LangGraph)
  │  35+ 工具、2 个子 Agent、15 个中间件
  │  MCP：默认进程内加载；也可切独立 MCP Server(:9000, Streamable HTTP)
ERP 后端 (:8081)
  │  仓库自带 Mock（FastAPI + PostgreSQL），实现 23 个业务接口
  └ 也可以改 ERP_BASE_URL 指向真实 Java ERP
```

## 2. 一次对话的链路

1. `POST /api/chat/stream` 建立 SSE
2. 先落库用户消息（断流也不会丢），再推第一个 `thinking`
3. Agent 图执行：入口审查路由 → 计划 → 工具/子 Agent → 评审（Grader）→ 输出
4. 生成过程中每 1.5 秒保存一次进度；`finally` 用 `asyncio.shield` 兜底
5. 需要人工确认时发 `interrupt`，前端渲染审批卡片，`POST /api/chat/{thread_id}/resume` 继续

## 3. 中间件栈

| # | 中间件 | 职责 |
|---|---|---|
| 1 | SandboxHealthMiddleware | 沙箱健康检查 + 自动重连 |
| 2 | HarnessPhaseMiddleware | 阶段状态机 + 规则/模型混合路由 |
| 3 | ContextInjectionMiddleware | 用户上下文注入 |
| 3.5 | WarmMemoryMiddleware | 每次模型调用前注入 WARM 摘要（≤4000 字符） |
| 4 | SkillsSyncMiddleware | 技能文件夹级增量同步 |
| 5 | UserSkillsRestoreMiddleware | 用户自定义技能恢复 |
| 6 | ToolsSummarizationMiddleware | 工具调用摘要 |
| 7 | MemoryUpdateMiddleware | 偏好抽取写入 |
| 8 | MemoryConsolidationMiddleware | 情节归档 + 遗忘扫描 |
| 9 | SandboxCircuitBreakerMiddleware | 沙箱熔断 |
| 10 | SafeRubricMiddleware | 框架 Rubric 审查；写操作后禁止自动重放 |
| 10.5 | ReviewExecutionGate | 按本轮工具记录升级评审 |
| 11 | ModelCallLimitMiddleware | 模型调用次数限制 |
| 12 | ToolCallLimitMiddleware | 工具调用次数限制 |

## 4. 工具

- **23 个 ERP 工具**（MCP，默认进程内）：供应商 5、零部件 5、订单 7、库存 6
- **外部 MCP（可选）**：`config/mcp.json` 配置的第三方服务，格式见 README
- **自定义工具**：chart_generator（26 种图表）、web_search（DeepSeek Responses API）、web_fetch、install_skill、list_user_skills、request_order_info、download_sandbox_file、list_sandbox_files、generate_document、generate_table_report
- **记忆工具**：read_memory、remember（`MEMORY_TOOLS_ENABLED=false` 时不注册）
- **技能读取**：list_skills / read_skill / read_skill_resource（渐进披露）

启动日志会打印实际数量，含外部 MCP 时典型值为 `45 MCP + 11 custom + 3 skill readers = 59`。

## 5. 三层记忆

| 层 | 内容 | 介质 | 策略 |
|---|---|---|---|
| HOT | 当前会话消息、工具结果、todo | LangGraph 状态 + MongoDB checkpoint | 供恢复用，归档不删原消息 |
| WARM | 有效语义记忆、近 7 天情节摘要 | MongoDBStore，动态注入 system prompt | 每次模型调用重读，摘要 ≤4000 字符 |
| COLD | 历史情节、程序记忆 | MongoDBStore，按需检索 | BM25 + 时间衰减 + 频次 + 置信度，默认保留 90 天 |

命名空间：

```
("memories", <user_id>, "semantic")     用户偏好等稳定事实
("memories", <user_id>, "episodic")     每次会话一条 ep_<thread_id>
("memories", <user_id>, "procedural")   经验/流程
("user-preferences", <user_id>)         WARM 投影（兼容旧数据）
```

读写集中在 `src/agent/memory/keeper.py`，没有跨记录事务；检索单类最多读 500 条。

## 6. 沙箱

- 7 层防护：只读根文件系统、`--tmpfs /tmp`、内存/CPU 上限、网络隔离、`cap-drop ALL`、seccomp、PID 限制
- 五态生命周期：预热池 → 认领 → MongoDB 缓存 → 新建 → 销毁
- 创建与同步在 `src/agent/backends/`；镜像由 `docker/Dockerfile.sandbox` 构建

## 7. API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat/stream` | SSE 流式对话 |
| POST | `/api/chat/{thread_id}/resume` | 中断恢复（approve / reject / edit / 补充信息） |
| GET | `/api/chat/{thread_id}/state` | 是否处于中断中 |
| GET | `/api/history?user_id=` | 会话列表 |
| GET | `/api/history/{thread_id}/messages` | 会话消息 |
| DELETE | `/api/history/{thread_id}` | 删除会话（硬删除） |
| GET | `/api/download/{filename}` | 下载生成的文档/图表 |
| GET | `/health` | 健康检查 |

`resume` 的 `edit` 决策由后端补成框架要求的 `edited_action`，并把散参数包回 `order_data`。

## 8. 关键配置

- `src/agent/harness_config.yaml`：审查策略（`mode: auto/always/never`、`strategy: hybrid/rules`）、路由超时、评分维度
- `src/agent/subagents/configs/*.yaml`：子 Agent 工具集与提示词
- `config/sandbox_config.toml`：沙箱资源与镜像
- `.env`：模型、数据库、ERP 地址、记忆开关（见 `.env.example`）

## 9. 评测与验收

见 `evals/`（业务验收、路由配对评测）与 `docs/` 下的验收记录。所有数字都是本地有限样本，不代表线上 SLA。

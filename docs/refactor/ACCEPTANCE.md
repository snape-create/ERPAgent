# 重构验收台账（ACCEPTANCE）

> 用法：每个任务卡完成后，由主代理（审计角色）填写一行「验收记录」。
> 只记录**可复现的证据**，不写主观评价。

## 1. 验收基线（每次验收都以此为准）

```bash
cd backend
uv run ruff check .            # 期望：All checks passed
uv run pytest tests/ -q        # 期望：157 passed, 10 skipped
uv run python -c "import app.gateway.app as g; print(len(g.app.openapi()['paths']))"   # 期望：12
uv run langgraph dev --no-browser --no-reload --port 2030 &
curl -s localhost:2030/ok                                                     # 期望：{"ok":true}
```

| 指标 | 基线值 | 说明 |
|---|---|---|
| pytest | 157 passed / 10 skipped | skipped 为四个手动验收脚本 |
| ruff | 0 error | `select = E,F,I,UP,W605` |
| 网关路由 | 12 | `/api/chat/*` 3 条、`/api/history*` 3 条、`/api/mcp/*` 2 条、download、root、health |
| MCP 工具 | 23 | `build_mcp().list_tools()` |
| 建图 | 27 nodes / 16 middlewares | 工具数随外部 MCP 可用性 51~59 浮动 |
| 沙箱上传 | 94 文件 | 清单只有 `skills/` 与 `erpflow/` |

## 2. 验收闸门（任一不过即打回）

| # | 闸门 | 判定方式 |
|---|---|---|
| G1 | 分层未被破坏 | `pytest tests/test_harness_boundary.py` 通过 |
| G2 | 无新增 skip/xfail | `git diff` 中不出现新增 `pytest.mark.skip` / `xfail` |
| G3 | 无测试断言被改弱 | `git diff tests/` 中不含对既有断言的删除或放宽 |
| G4 | 改动在任务卡白名单内 | `git status --short` 的文件集合 ⊆ 任务卡允许路径 |
| G5 | 配置/路径不变量 | `grep -rn "os.getenv" backend/app backend/packages | grep -v config/` 无新增 |
| G6 | 基线不退化 | 上述基线表数字不下降 |
| G7 | 前端契约不变 | `app/services/sse.py` 的事件名集合未变化 |

## 3. 验收记录

| 日期 | 任务 | 执行者 | 证据 | 结论 |
|---|---|---|---|---|
| 2026-11-18 | P1 配置单一入口 | 主代理（历史） | `ruff clean`；`test_settings_priority.py` 42 项；`pytest 157/10` | 通过 |
| 2026-11-18 | 目录收敛（agent/review/skills + 删 env_utils + tar 收窄） | 主代理（历史） | 顶层 13→7；上传 94 文件；`pytest 157/10`；`langgraph /ok` | 通过 |
| 2026-11-18 | W2 CI workflow | codebuddy | 新增 `.github/workflows/backend-tests.yml`（唯一新增路径，G4 过）；`yaml.safe_load` 通过，job=`test`，5 步顺序 Checkout→setup-uv→uv sync→ruff→pytest；`working-directory: backend`、`timeout-minutes: 15`、`LANGSMITH_TRACING=false` 均符合任务卡 | 通过 |
| 2026-11-18 | W4 文档/环境变量卫生 | codebuddy | `.env.example` 死键 0 处、新增 `TYPESAFE_URL`/`GATEWAY_PORT`/`CORS_ORIGINS`/`LANGSMITH_TRACING` 齐全；`ARCHITECTURE.md` 端点表含 `/api/mcp/servers{,/{name}/toggle}`（A1~A3 过）；改动文件仅 README/.env.example/ARCHITECTURE（A4 过） | 通过 |
| 2026-11-18 | W1a 事件映射设计 | pi 两次后台运行无输出（0 字节），改派 codebuddy 完成 | `docs/refactor/reports/W1a-event-mapping.md`（271 行）：SDK 参数实为 `stream_subgraphs`、namespace 在 `event.split("|")`、messages 消息已序列化为 dict、中断变 `{"value","id"}`、resume 须传 dict、run 前须建 thread；12 事件映射全覆盖（4 项无对等需网关合成、5 项需适配）；风险 R1~R12 各带文件行号；三步切换路径。A1~A4 全过 | 通过 |

| 2026-11-18 | W1b 事件适配层 | codebuddy | 新增 `langgraph_adapter.py`；`chat_turn` 接入 `message_field` / `unwrap_interrupt`；`tests/test_langgraph_adapter.py` 12 passed；全量 169 passed；ruff clean；`sse.py` 未变 | 通过 |
| 2026-11-18 | W1c 网关切 SDK（带开关） | codebuddy | 新增 `langgraph_client.py`；`chat_stream` 双路径；`tests/test_chat_stream_sdk.py` 7 passed（断言 stream_mode/stream_subgraphs/config/user_id+username/threads.create/resume 只传 command/state dict 中断/懒加载单例）；全量 176 passed；ruff clean | 通过 |
| 2026-11-18 | W1c-fix UUID | codebuddy | `server_thread_id()` = uuid5 确定性映射；SDK 侧统一映射、对外原 id；全量 181 passed；实机 UUID 错误消失 | 通过 |
| 2026-11-18 | W1c-fix2/3 内部调用泄露 | codebuddy | 首轮泄露 23 条 `tool_args`（ReviewRoute）→ 二轮改为 `OrderIntentDecision` 仍有 1+22 条 → 三轮按「名称+标签+已判定集合」整体抑制；全量 192 passed；实机 0 泄露 | 通过（两轮返工） |
| 2026-11-18 | W1d 切默认 + 删旧路径 | codebuddy | 删除 `agent_session.py`；`app/` 无 `agent_sessions`/`sandbox_manager`/`acquire_lease` 引用（H1）；192 passed/10 skipped（H2）；ruff clean（H3）；默认配置启动网关无 “Warm pool refill”（H5 过） | 通过 |
| 2026-11-18 | W1 实机联调（5 轮） | 主代理 | 对话：`thinking×2→phase→thinking(end)→token×36→done`；中断：`order_info_supplement` 卡片；resume(supplement)→`part_query` 工具四事件→二次补充卡片；resume(decisions=approve)→`hitl_approval` 卡片含解包后的 `tool_args`→审批后 `tool_result/tool_end/review_result/done`；`/state` 返回 `interrupted:true` + 卡片 | 通过 |
| 2026-11-18 | W3 langgraph-api 升级调查 | codebuddy | `docs/refactor/reports/W3-langgraph-api-upgrade.md`（259 行）：6 个依赖面在 0.18.3 中仍存在；主要成本是依赖大版本跃迁；结论“现在升”。**注**：其前提“默认仍 inprocess”在 W1d 后已不成立，结论需按新的默认路径重估 | 有条件采纳 |

| 2026-11-18 | W5 graph 工厂去阻塞 | codebuddy 实现 + 主代理实机 | 新增 `agent/graph_cache.py`（线程池装配 + 每用户缓存 + 进程预热），`graph.py` 只做配置解析；新增 `tests/test_graph_factory.py`；195 passed / ruff clean；**不带 `--allow-blocking`** 启动 server，实机 5 轮全过（对话 / 补充中断 / 二次 resume / 审批卡片 / approve 后 `tool_result+review_result+done`），`BlockingError` 全程 0 次；`order_create` 真实写入 Mock ERP（id=6, PO-W5-001, 20.0） | 通过 |
| 2026-11-18 | W5 观察项 | 主代理 | 5 轮里 `Main agent created` 出现 2 次（预期 1 次），即缓存命中 3/5；未影响正确性，但需确认是否被 `_pending` 竞态或多实例调用触发。记入 W6 | 待跟进 |

## 4. 打回处理

- 打回时在记录表写「失败项 + 复现命令 + 最小修复建议」，重新派单给同一执行者（附上次失败输出）。
- 同一任务连续 2 次打回，改写任务卡（缩小范围或换执行者），不复用原卡。

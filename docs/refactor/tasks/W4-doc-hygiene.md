# 任务卡 W4 — 文档与环境变量小卫生

**执行者**：codebuddy **依赖**：无 **预估**：20 分钟

## 允许改动的文件（白名单，超出即打回）

1. `.env.example`（仓库根）
2. `docs/ARCHITECTURE.md`
3. （可选）`README.md` —— 仅当其中引用了下面要删的变量名

**不得**改任何 `.py`、`tests/`、`.env`。

## 内容

### 1. `.env.example`

- 删除已无代码引用的键：`WEB_SEARCH_MODEL`、`DEEPSEEK_RESPONSES_URL`
- 补充网关进程参数（注释形式给默认值即可）：
  - `GATEWAY_HOST`（默认 0.0.0.0）
  - `GATEWAY_PORT`（默认 8000）
  - `CORS_ORIGINS`（默认 `http://localhost:3000`，逗号分隔）
- 补充决策模型端点：`TYPESAFE_URL`（默认 `https://api.typesafe.ai/v1/systemone`）
- 补充追踪开关：`LANGSMITH_TRACING=false`、`LANGSMITH_PROJECT=erp-agent`
- 保持原有分组与注释风格，不要重排无关行

### 2. `docs/ARCHITECTURE.md`

- “API 端点”表格若缺少 `/api/mcp/*`（`GET /api/mcp/servers`、`POST /api/mcp/toggle`），补齐；已有则核对说明文字
- 目录结构段若仍写 `Makefile` 或 `PYTHONPATH=.` 启动方式，改为 `uv run python -m app.gateway.app`（项目已弃用 Make 与 PYTHONPATH）

## 验收（主代理执行）

| 闸门 | 判定 |
|---|---|
| G4 | `git status --short` 仅出现白名单内文件 |
| A1 | `.env.example` 中 `grep -c "WEB_SEARCH_MODEL\|DEEPSEEK_RESPONSES_URL"` == 0 |
| A2 | 新增键名齐全：`GATEWAY_PORT`、`CORS_ORIGINS`、`TYPESAFE_URL`、`LANGSMITH_TRACING` |
| A3 | `docs/ARCHITECTURE.md` 中 `/api/mcp` 出现在端点表内 |
| A4 | 未改动任何 `.py` / `tests/` |

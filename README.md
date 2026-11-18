# ERP 智能采购助手

面向摩托车零部件采购的对话工作台：查库存、比供应商、下单走审批。写操作不会直接落库，先弹审批卡片，人可以改参数、批准或拒绝。

![空状态](docs/images/01-welcome.png)

| 对话与数据表格 | 写入前的人工审批 |
|---|---|
| ![对话](docs/images/02-conversation.png) | ![审批](docs/images/03-approval.png) |

## 实测效果

| 新增采购订单：缺字段弹表单 → 补齐 → 审批 → 落库 | 零部件分析：沙箱生成图表并导出内嵌 |
|---|---|
| <img src="result/新增采购订单.png" width="420"> | <img src="result/零件分析.png" width="420"> |

## 能做什么

- **库存预警**：按安全库存算缺口，可直接生成补货建议
- **零部件 / 供应商查询**：价格、型号、关联供应商、信用评级
- **订单全流程**：创建、修改、状态推进（只能逐级前进），写入前必须人工确认
- **图表与文档**：26 种图表、Markdown / HTML / CSV / JSON 导出并可下载
- **记忆**：跨会话记住偏好；**技能**：按需加载，不提前塞满上下文
- **沙箱**：生成的代码在隔离容器里执行

## 环境要求

| 依赖 | 版本 | 用途 |
|---|---|---|
| Docker | 24+ | MongoDB、PostgreSQL、代码沙箱 |
| Python | 3.13+ | 后端与 Mock ERP |
| [uv](https://docs.astral.sh/uv/) | 最新 | 装 Python 依赖 |
| Node.js | 18+ | 前端 |
| DeepSeek API Key | — | 对话、评审、联网搜索共用 |

## 快速开始

### 1. 起数据库（Docker）

```bash
cp .env.example .env          # 先填 DEEPSEEK_API_KEY
docker compose up -d mongo postgres
```

`.env` 里两处改成容器地址（`.env.example` 默认就是这两个）：

```dotenv
MONGODB_URI=mongodb://root:root123@localhost:27017/erp_agent?authSource=admin
ERP_DB_URL=postgresql+psycopg2://postgres:postgres123@localhost:5432/erp_agent
```

### 2. 构建沙箱镜像

代码沙箱由后端通过 Docker API 按需创建，镜像要提前构建好：

```bash
docker build -f docker/Dockerfile.sandbox -t erp-agent-sandbox:latest docker/
```

也可以用 compose 构建（`sandbox` 服务声明了 `pull_policy: build`，不会去 registry 拉取）：

```bash
docker compose build sandbox
# 或者直接 up：本地没有镜像时会自动构建
docker compose up -d
```

> 只构建镜像，不需要常驻容器；每次对话后端自己起临时沙箱。
> 镜像名要和 `.env` 里的 `SANDBOX_IMAGE` 一致。

### 3. 初始化 ERP 数据库

首次启动 Mock ERP 会自动建表并灌入示例数据，**这一步可以跳过**。

如果要恢复示例数据、或重建环境，手动执行种子脚本（幂等，可重复跑）：

```bash
docker compose exec -T postgres psql -U postgres -d erp_agent < backend/erp_mock/seed_data.sql
```

脚本内容：6 家供应商、15 个零部件、15 条库存、3 张示例订单；文件在 `backend/erp_mock/seed_data.sql`。

### 4. 安装 Python 依赖

```bash
cd backend
uv sync
```

> 后端是 uv workspace 两层：`backend/pyproject.toml`（网关，不打包）+ `packages/harness`（`erpflow-harness`，可安装）。`uv sync` 一次装好两者并建 `backend/.venv`；`erp_mock/` 是 Mock ERP 源码目录，随目录直接运行。

### 5. 启动三个进程

**终端 1：Mock ERP（:8081）**

```bash
cd backend
uv run python -m erp_mock.main
```

这是一个 FastAPI + PostgreSQL 的本地 ERP，实现了 Agent 需要的 23 个业务接口。要接真实 ERP 时这一步不用起，改 `.env` 的 `ERP_BASE_URL` 即可。

**终端 2：Agent 服务（LangGraph Server，:2024）**

```bash
cd backend
uv run langgraph dev --no-browser --no-reload
```

图入口在 `langgraph.json`：`erpflow.graph:make_lead_agent`，checkpointer 由 `erpflow/checkpointer.py` 提供。

**终端 3：后端网关（:8000）**

```bash
cd backend
uv run python -m app.gateway.app
```

> 两个入口都用 `python -m`，cwd 自动进 `sys.path`，不需要设置 `PYTHONPATH`。

23 个 ERP 工具默认进程内加载，不需要单独启动 MCP Server。

**终端 4：前端（:3000）**

```bash
cd frontend
npm install
npm run dev
```

### 6. 打开 http://localhost:3000

## 可选：接入外部 MCP

项目默认只加载内置的 ERP 工具。要接第三方 MCP 服务，新建 `backend/config/mcp.json`（该文件默认不存在）：

```json
{
  "mcpServers": {
    "bing-search": {
      "command": "bing-cn-mcp",
      "args": []
    },
    "amap-maps": {
      "type": "streamable_http",
      "url": "https://mcp.api-inference.modelscope.net/<your-id>/mcp"
    }
  }
}
```

- `command` 是本地进程方式，需要该命令已安装并可执行
- `streamable_http` 是远程服务，填对方给的 URL
- 接得越多，冷启动越慢（每个远程服务要建连，失败还会重试），工具也会挤占模型上下文。**不用的服务不要写进去。**
- 临时不想删配置时可以只禁用：`.env` 里加 `MCP_DISABLED_SERVERS=bing-search,amap-maps,12306`，重启即生效，内置 ERP 工具不受影响。

## 换成你自己的 ERP

改 `.env`：

```dotenv
ERP_BASE_URL=http://your-erp-host:8081
```

接口契约见 `backend/erp_mock/main.py`（23 个接口）。接了真实 ERP 就不用启动 Mock。

## 目录结构

后端是 **两层单向依赖**：`app`（网关）可以 import `erpflow`，**`erpflow` 绝不能 import `app`**——由 `tests/test_harness_boundary.py` 静态扫描守住。

```
├── frontend/                    # Next.js 前端
├── backend/
│   ├── app/                     # 网关层 app.*（不打包，python -m app.gateway.app）
│   │   ├── gateway/             #   FastAPI 装配 + routers（chat / history / mcp）
│   │   ├── services/            #   应用服务：Agent 会话、SSE 编排
│   │   ├── repositories/        #   数据访问：Mongo / 会话消息
│   │   └── mcp_server/          #   独立 MCP Server 进程（:9000，可选）
│   ├── packages/harness/        # 核心层 erpflow-harness（可安装、可独立演进）
│   │   └── erpflow/
│   │       ├── graph.py         #   LangGraph Server 图入口（make_lead_agent）
│   │       ├── checkpointer.py  #   checkpointer 工厂（Mongo / 内存兜底）
│   │       ├── agent/           #   Agent 装配（build）+ 沙箱引导（bootstrap）
│   │       ├── middlewares/ memory/           #   中间件 / 三层记忆
│   │       ├── review/          #   阶段机 + 路由策略 + 决策模型适配（config.yaml）
│   │       ├── skills/          #   技能加载与渐进披露
│   │       ├── backends/        #   Docker 沙箱 + 池维护 + 孤儿清理
│   │       ├── tools/           #   自定义工具 + MCP 客户端
│   │       ├── integrations/erp #   ERP HTTP 客户端 + 23 个领域工具（唯一实现）
│   │       ├── subagents/       #   YAML 声明式子 Agent
│   │       ├── store.py         #   MongoDBStore + 进程级单例
│   │       └── config/          #   配置单一入口：paths / sandbox / settings / llm
│   ├── erp_mock/                # Mock ERP（FastAPI + PostgreSQL）+ 种子 SQL
│   ├── skills/                  # 技能数据资产（按需加载）
│   ├── config/                  # 沙箱 TOML、外部 MCP 配置
│   ├── tests/                   # 扁平 pytest + 架构边界守卫
│   ├── langgraph.json           # LangGraph Server 入口清单
│   ├── pyproject.toml           # workspace 根（网关依赖，不打包）
│   ├── ruff.toml
│   └── uv.lock
├── docker/                      # 沙箱镜像 Dockerfile
├── evals/                       # 业务验收与路由评测
├── docs/                        # 架构说明与验收记录
├── compose.yaml                 # MongoDB / PostgreSQL
└── .env.example
```

## 常见问题

**前端能打开，但发消息没反应**
后端没起或端口不对。先看 http://localhost:8000/health 是否返回 `{"status":"healthy"}`。

**报沙箱镜像不存在**
先执行第 2 步构建 `erp-agent-sandbox:latest`，并确认 `.env` 的 `SANDBOX_IMAGE` 与镜像名一致。

**回复不是流式，而是一次性蹦出来**
前端 `next.config.ts` 里的 `compress: false` 不能删。Next 的 gzip 会把 SSE 整条缓冲，改成压缩后就不再逐字输出。

**历史会话点进去是空的**
确认后端是当前版本：旧版本只在流正常结束时才保存消息，切换会话会丢。现在用户消息即时入库、生成中定时保存、断流也会兜底保存。

**数据库连不上**
`docker compose ps` 看容器是否 healthy；`.env` 里的 `MONGODB_URI` 必须带 `authSource=admin` 和账号密码。

## 更多文档

- [架构与实现](docs/ARCHITECTURE.md)：中间件、记忆、工具、API 端点
- [Skills 渐进披露](docs/PROGRESSIVE_SKILLS.md)
- [业务验收记录](evals/business/README.md) 与 [Agent 能力实测](docs/AGENT_LIVE_TESTS_20261023.md)

## 已知问题

- 断流时保存的是半截回答，没有 `partial` 标记，后续追问会把它带进上下文
- 冷启动首条消息较慢：外部 MCP 连接、项目文件上传沙箱、Skills 首次全量同步各占数秒

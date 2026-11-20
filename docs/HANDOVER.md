# ERP Agent 交接文档

本文覆盖当前前端聊天体验、后端 SSE/审查链路、产物下载与查看页，以及后续维护时最容易被踩到的边界。

---

## 1. 项目结构

```
backend/
  app/
    gateway/                 FastAPI 网关：对话、历史、上传、MCP、文件服务
    services/
      chat_stream.py        SSE 生命周期、心跳、done、最终落库
      chat_turn.py          chunk → SSE 事件、展示消息持久化
      langgraph_adapter.py  LangGraph SDK StreamPart → 统一输入
      sse.py                SSE 线格式与响应头
    repositories/           MongoDB 读写
  packages/harness/erpflow/
    agent/build.py          Agent / 中间件 / 工具装配
    middlewares/review_gate.py  rubric 前置路由与工具额度止血
    review/                 阶段、评审策略、JEV 路由
    memory/prompts.py       主 Agent 系统提示词

frontend/
  src/app/page.tsx          页面装配、历史缓存、线程切换
  src/app/view/page.tsx     报告查看页（/view?href=...）
  src/hooks/useChat.ts      对话状态机、parts/工具/最终稿边界
  src/hooks/useSSE.ts       SSE 解析结果按 rAF 批量派发
  src/components/chat/
    MessageBubble.tsx       答案区 / 过程入口 / 抽屉 / footer
    ProcessHeader.tsx       “正在思考”入口
    ProcessDrawer.tsx       右侧执行详情（阶段/工具/草稿）
    StreamingMarkdown.tsx   按 block memo 的流式 Markdown
    Markdown.tsx            Markdown 渲染、产物链接下载
    ArtifactPanel.tsx       产物预览/下载/新标签
  src/lib/artifact-links.ts 产物链接归一化、下载与 viewer 地址
```

---

## 2. 启动与验证

```bash
# 依赖
docker compose up -d mongo postgres
cd backend && uv run langgraph dev --no-browser --no-reload   # :2024
cd backend && uv run python -m app.gateway.app                # :8000
cd frontend && npm install && npm run dev                     # :3000

# 测试
cd backend && uv run pytest -q
cd backend && uv run ruff check app tests
cd frontend && npx tsc --noEmit
cd frontend && npm run build
```

网关文件服务：

- `/api/artifacts/{user_id}/{path}`：用户 outputs / charts / 文档
- `/api/download/{filename}`：兼容旧下载目录
- 默认文本/图片/PDF 为 `inline`；`?download=1` 时强制 `attachment`

---

## 3. 后端 SSE 链路

### 3.1 入口

`app/gateway/routers/chat.py`

- `POST /api/chat/stream` → `stream_chat_response`
- `POST /api/chat/{thread_id}/resume` → resume
- `GET /api/chat/{thread_id}/state` → 是否处于中断

`app/services/chat_stream.py`

1. `_prepare_turn` 读取/写入用户消息
2. `client.runs.stream` 消费 LangGraph Server 流
3. `_adapt_server_stream` 抹平 SDK StreamPart 差异
4. `_stream_turn` 调用 `ChatTurn` 产生事件
5. `_with_heartbeat` 在 10 秒无事件时写 `: keepalive`
6. 正常结束时先发 `done`，再执行 `finish_completed` 最终落库
7. `finally` 调用 `_final_persist` 兜底

### 3.2 事件契约

```text
thinking       {"status":"start"|"end"}
reasoning      {"content": "...", "source": "main"|"analyst"|"order"}
token          {"content": "...", "source": ...}
tool_start     {"name": "...", "id": "..."}
tool_args      {"args": "..."}
tool_result    {"name": "...", "id": "...", "content": "..."}
tool_end       {"id": "..."}
phase          {"phase": "...", "label": "..."}
todo_update    {"todos": ...}
review_result  {"verdict":"satisfied|needs_revision|failed", ...}
revision       {"finalStartIndex": 3, "firstRevisionIndex": 1}
interrupt      { ...HITL 载荷 }
done           {"thread_id": "...", "interrupted": bool}
error          {"message": "..."}
```

前端主要消费：`thinking / reasoning / token / tool_* / phase / todo_update / revision / interrupt / done / error`。
`review_result` 当前只保留在链路里，主界面未直接渲染。

### 3.3 `ChatTurn` 的展示消息

`backend/app/services/chat_turn.py`

每条 assistant 展示消息包含：

```json
{
  "role": "assistant",
  "content": "聚合后的全部正文（含草稿）",
  "toolCalls": [...],
  "parts": [
    { "type": "text", "text": "..." },
    { "type": "tool", "call": { ... } }
  ],
  "finalStartIndex": 3,
  "firstRevisionIndex": 1,
  "startedAt": 1760000000000,
  "endedAt": 1760000012345
}
```

- `parts`：保留正文/工具的真实顺序。
- `finalStartIndex`：最后一版修订在 `parts` 中的起点；`parts[finalStartIndex:]` 由前端作为最终稿。
- `firstRevisionIndex`：第一次修订时的草稿结束位置；用于“第一版草稿保持可见”。
- `startedAt / endedAt`：整轮耗时。
- `content / toolCalls` 仍然保留，兼容旧消息和产物提取。

### 3.4 持久化

- `maybe_persist` 只做节流判断，后台任务写入，不阻塞 token 循环。
- `flush_persist` 在完成/中断/断流时等待在途写入并保存最终快照。
- 最终快照会带 `endedAt`；流式中间快照 `endedAt = null`。

---

## 4. 审查、草稿与工具额度

### 4.1 为什么会有“草稿”

`SafeRubricMiddleware`（`deepagents.RubricMiddleware`）在模型输出后做 rubric 评审：

- `satisfied`：通过；
- `needs_revision`：注入修订要求，模型继续生成；
- `failed` / 额度耗尽：结束。

因此一次用户请求可能产生多版模型正文。后端保留全部草稿，并通过 `revision` 通知前端划分“过程 / 最终稿”。

### 4.2 工具额度止血

`backend/packages/harness/erpflow/middlewares/review_gate.py`

- `execution_signals` 统计 `_review_start` 后的工具消息：
  - 出现 `Tool call limit exceeded`；
  - 或工具数达到 `MAX_TOOL_CALLS`；
  - → `tool_budget_exhausted`；
- `ReviewExecutionGate` 在额度耗尽时直接 `skip`，不再触发复审；
- `SafeRubricMiddleware` 看到 `_review_budget_exhausted` 时短路，不评审；
- `ReviewPolicy.finish` 每轮重置该标记。

目的：避免“评审要求补证 → 工具全部被拦截 → 模型连续道歉稿”。

### 4.3 修订措辞

`backend/packages/harness/erpflow/memory/prompts.py`

主系统提示词明确：

- 评审是同一轮内部检查，不是新的用户轮次；
- 修订后直接输出完整最终回答；
- 禁止“上一轮 / 上一次回复 / 初稿 / 修订稿 / 先说明一处更正”等过程性表述。

如果以后仍出现内部修订痕迹，优先检查：
1. 系统提示词是否被覆盖；
2. rubric 注入的 human message 是否被模型当成新回合；
3. 是否需要额外的 before_model 指令中间件。

---

## 5. 前端对话与渲染

### 5.1 `useChat`

职责：

- 维护 `messages / streaming / activityLabel / lastEventAt / phase / finalizedAt`
- 维护本轮 ref：
  - `assistantMsgRef`
  - `toolCallsRef`
  - `partsRef`
  - `finalStartIndexRef`
  - `firstRevisionIndexRef`
  - `turnStartedAtRef`
- `revision` 事件：
  - 更新 `finalStartIndexRef`；
  - 第一次修订时更新 `firstRevisionIndexRef`；
  - `updateAssistantMessage` 同步到消息。
- `done / error / onError / onComplete` 会给最后一条 assistant 消息补 `endedAt`。

### 5.2 `useSSE`

- `SSEParser` 解析 `event: / data:`
- 解析结果先入队，按 `requestAnimationFrame` 批量派发
- 流结束/错误前先同步 flush，再 `onComplete / onError`
- 心跳注释行会被解析器忽略

### 5.3 答案区与过程区

`MessageBubble.tsx` 的切分规则：

- `parts[finalStartIndex:]` 中的 **text** → 答案区；
- 所有 **tool** → 右侧抽屉；
- `parts[:finalStartIndex]` 中的 text → 过程/草稿；
- `ProcessHeader`：正文上方一行“正在思考 / 思考完成”，点击打开 `ProcessDrawer`；
- `ProcessDrawer`：状态摘要、5 阶段、工具时间线、草稿正文；
- 工具参数/结果 JSON 只在抽屉里展开，不进入正文。

### 5.4 第一版草稿保持可见

关键字段：`firstRevisionIndex`。

- 第一次修订前，答案区正常流式显示第一版；
- 收到 `revision` 后：
  - 第一版草稿继续留在答案区；
  - 末尾显示“审查中，完成后自动更新”+ 冒泡点；
  - 修订稿在后台流式写入，过程抽屉里可看到当前修订稿；
- 最终稿完成后，答案区一次性替换为最终稿；
- 避免“先清空、再重新输出”的跳变。

### 5.5 流式 Markdown

`StreamingMarkdown.tsx`：

- 按顶层 block 切分；
- 稳定 block 用 `memo`，只重解析最后一段；
- 围栏代码、列表、表格尽量不拆断；
- `Markdown.tsx` 负责最终渲染和链接行为。

### 5.6 产物下载与查看页

`frontend/src/lib/artifact-links.ts`

- 只接受 `/api/download/` 与 `/api/artifacts/` 前缀；
- 外部 host 会被收敛为同源相对路径，避免任意跳转；
- `withDownloadFlag` 补 `?download=1`；
- `viewerHref` 生成 `/view?href=...`。

`Markdown.tsx`

- 产物链接**默认下载**：`?download=1` + `download` 属性；
- 不再把 `.md` 链接拦截成预览弹窗；
- 外部普通链接仍然新标签打开；
- 图片仍以内嵌图片展示，点击打开原图。

`ArtifactPanel.tsx`

- `预览`：弹窗内渲染；
- `下载`：`?download=1`；
- `新标签` / `新标签打开`：
  - 文本类 → `/view` 渲染页；
  - PDF/图片/其它 → 原始地址；
  - HTML → 不在同源新标签执行，只走 sandbox iframe 预览。

`app/view/page.tsx`

- 从 `?href=` 读取同源产物路径；
- Markdown 渲染为阅读页；
- JSON 美化、文本 `<pre>`、HTML sandbox iframe、PDF iframe、图片 `<img>`；
- 顶部提供“下载”。

---

## 6. 关键设计取舍

1. **不把原始 CoT 放进正文**
   - `reasoning_content` 发 `reasoning` 事件，当前只驱动活动状态；
   - 不展示、不落库，避免体积和敏感信息问题。

2. **工具明细与答案分离**
   - 答案区只承载最终文本；
   - 工具原始 JSON 在右侧抽屉；
   - 好处是正文可读、证据可追溯。

3. **审查过程对用户可见但不污染答案**
   - 草稿保留在过程抽屉；
   - 第一版草稿在修订期间保持可见；
   - 最终稿只替换一次，不清空重填。

4. **下载与预览分离**
   - 链接 = 下载；
   - 预览 = `/view` 或产物区预览弹窗；
   - HTML 不在同源新标签直接执行。

---

## 7. 已知限制与后续可做项

- **终稿选择**：目前最终稿 = 最后一版修订。额度止血后不会再出现“工具耗尽后继续修订”，但没有实现“最后一版通过稿”作为终稿的选择策略。
- **旧历史消息**：没有 `finalStartIndex / firstRevisionIndex` 的旧数据按 0 处理；旧会话的草稿在抽屉里，答案区显示聚合正文/最后一版。
- **原始 CoT**：默认不展示。如需展示，需要单独设计折叠区和脱敏/留存策略。
- **后台线程并发**：`partsRef / toolCallsRef / finalStartIndexRef` 仍是全局 ref；切换会话时已有既有设计，后续可改为按 thread 隔离。
- **HTML 新标签**：出于安全考虑不直接打开；如需要，应继续走 sandbox viewer。
- **报告导出格式**：目前报告多为 `.md`，通过 `/view` 渲染；如果希望浏览器原生打开，可以让工具直接生成 `.html`。
- **移动端**：本项目只做 Web 页面，没有独立移动客户端；窄窗口按普通响应式处理。

---

## 8. 常见排查

### 流式卡住

1. 看后端是否每 10 秒有 `: keepalive`；
2. 看 `done` 是否已经发出，前端是否仍在等后续写库；
3. 看工具是否停在 `running`，前端断流会把它们收口为 `error`。

### 出现“上一轮/初稿”措辞

1. 检查 `MAIN_SYSTEM_PROMPT` 是否生效；
2. 检查是否有自定义 prompt 覆盖；
3. 检查 rubric 注入的 human message 是否被模型当成新一轮。

### 下载变成显示正文

1. 检查链接是否被旧版预览逻辑拦截；
2. 确认 `MarkdownLink` 已走 `?download=1`；
3. 确认网关返回 `Content-Disposition: attachment`。

### 新标签空白

1. 确认打开的是 `/view?href=...` 而不是原始 `.md`；
2. 确认 `href` 在同一前缀下；
3. 检查 viewer 的 fetch 是否 200。

---

## 9. 修改入口速查

| 想改什么 | 改哪里 |
|---|---|
| SSE 事件 | `backend/app/services/chat_turn.py` |
| 流生命周期 / 心跳 | `backend/app/services/chat_stream.py` |
| 审查 / 工具额度 | `packages/harness/erpflow/middlewares/review_gate.py` |
| 系统提示词 | `packages/harness/erpflow/memory/prompts.py` |
| 消息状态与最终稿索引 | `frontend/src/hooks/useChat.ts` |
| 答案/过程切分 | `frontend/src/components/chat/MessageBubble.tsx` |
| 状态入口 | `frontend/src/components/chat/ProcessHeader.tsx` |
| 右侧执行详情 | `frontend/src/components/chat/ProcessDrawer.tsx` |
| 流式 Markdown | `frontend/src/components/chat/StreamingMarkdown.tsx` |
| 下载/链接规则 | `frontend/src/lib/artifact-links.ts` |
| 报告查看页 | `frontend/src/app/view/page.tsx` |
| 产物面板 | `frontend/src/components/chat/ArtifactPanel.tsx` |

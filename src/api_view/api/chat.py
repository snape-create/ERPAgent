"""
核心 SSE 流式对话 + 中断检测 + 中断恢复 + 展示消息持久化

关键设计：
- 双模式流: stream_mode=["messages", "values"], subgraphs=True
- 输出格式: (namespace, mode, data) 三元组
- 中断检测在 messages 处理之前（values 流优先）
- display_messages 实时累积（用户消息 + 助手回复 + 工具调用）
- SSE 事件协议: token / tool_start / tool_args / tool_result / tool_end / interrupt / done
"""
import json
import uuid
from typing import AsyncGenerator
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from ..agent_loader import agent_loader
from ...agent.schema import ChatRequest, ResumeRequest
from ...agent.log_utils import web_logger

router = APIRouter(prefix="/api/chat", tags=["chat"])


def sse_event(event: str, data: dict) -> str:
    """格式化 SSE 事件（严格遵循 event:/data: 协议）"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def unwrap_order_args(args: dict) -> dict:
    """把 ``{"order_data": "<json 字符串>"}`` 摊平成单据字段。

    MCP 的 order_create / order_update 签名是 ``order_data: str``，模型实际传的
    是 JSON 字符串。不拆开的话审批卡片只能拿到一个字符串字段，既画不出明细表，
    也判断不出这是订单类写操作。解析失败时原样返回，卡片退化成键值表。
    """
    raw = args.get("order_data")
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return args
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return args
    if not isinstance(parsed, dict):
        return args
    # order_update 的 args 还带 id，保留其它键，单据字段覆盖到顶层
    merged = {key: value for key, value in args.items() if key != "order_data"}
    merged.update(parsed)
    return merged


def normalize_edit_decisions(decisions: list) -> list:
    """把前端「改完参数再提交」的 edit 决策翻译成框架要求的形状。

    前端只发 ``{"type": "edit", "tool_name": ..., "args": {...}}``；LangGraph 的
    HumanInTheLoop 要求 ``edited_action`` 包裹，且工具名被 Literal 钉死。另外
    order_* 工具的参数外层必须是 ``{"order_data": "<json 字符串>"}``，否则工具
    拿到的是散字段，会直接报参数错误。
    """
    normalized = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        if decision.get("type") != "edit" or "edited_action" in decision:
            normalized.append(decision)
            continue

        tool_name = decision.get("tool_name") or ""
        args = decision.get("args")
        if not isinstance(args, dict):
            args = {}
        if tool_name in ("order_create", "order_update"):
            args = {"order_data": json.dumps(args, ensure_ascii=False)}
        normalized.append({
            "type": "edit",
            "edited_action": {"name": tool_name, "args": args},
        })
    return normalized


def flatten_hitl_actions(interrupt_value: dict) -> dict:
    """把 HITL 中断的 action_requests 拍平成前端可直接渲染的结构。

    LangGraph 的 action_requests 形如
    ``[{"name": "order_create", "args": {...}, "description": "..."}]``，
    整体塞给前端会让 Object.entries() 只能拿到数组下标，卡片渲染不出单据内容。
    这里取第一条动作把 args 提到顶层；一次中断含多个动作时保留数量供前端提示。
    """
    raw = interrupt_value.get("action_requests")
    if isinstance(raw, dict):
        actions = [raw]
    elif isinstance(raw, list):
        actions = [item for item in raw if isinstance(item, dict)]
    else:
        actions = []

    first = actions[0] if actions else {}
    args = first.get("args")
    if not isinstance(args, dict):
        args = {}
    args = unwrap_order_args(args)
    return {
        "tool_name": first.get("name") or interrupt_value.get("tool_name", ""),
        "tool_description": first.get("description", ""),
        "tool_args": args,
        "order_data": args,
        "action_count": len(actions),
    }


ORDER_STATUS_NAMES = {0: "待审核", 1: "已审核", 2: "已发货", 3: "已收货", 4: "已完成"}


async def enrich_status_change(payload: dict) -> dict:
    """状态变更的审批卡片需要「当前 → 目标」，否则用户看不出是前进还是回退。

    只对 order_update_status 生效。读取失败时保持原样，前端会降级成只显示目标状态。
    """
    if payload.get("tool_name") != "order_update_status":
        return payload

    args = payload.get("tool_args") or {}
    order_id = args.get("id")
    if order_id is None:
        return payload

    try:
        from ...mcp_server.http_base import erp_client

        current = await erp_client.get(f"/api/orders/get/{order_id}")
        order = current.get("data") if isinstance(current.get("data"), dict) else current
        if not isinstance(order, dict) or order.get("status") is None:
            return payload
        payload["order_number"] = order.get("orderNumber")
        payload["current_status"] = order.get("status")
        payload["current_status_label"] = ORDER_STATUS_NAMES.get(order.get("status"))
        payload["target_status_label"] = ORDER_STATUS_NAMES.get(args.get("status"))
    except Exception as exc:
        web_logger.warning(f"Failed to enrich status change interrupt: {exc}")
    return payload


# Harness 阶段 → 前端展示文案
PHASE_LABELS = {
    "planning": "📝 规划中",
    "executing": "⚙️ 执行中",
    "reviewing": "🔍 审查中",
    "result": "📊 已完成",
    "thinking": "💭 思考中",
}


from ...agent.observability import traced_stream, traced_config, event


@traced_stream
async def stream_chat_response(
    message: str,
    thread_id: str,
    user_id: str = "default_user",
    username: str = "用户",
    resume_data: dict = None,
) -> AsyncGenerator[str, None]:
    """
    核心流式响应生成器

    双流模式（subgraphs=True 时输出为 3-tuple）：
    - (namespace, "values", data): 检测中断（interrupts 字段）
    - (namespace, "messages", data): 逐 token 输出 + 工具调用事件

    消息累积：
    - 实时将 assistant 回复追加到 display_messages
    - 流结束后持久化到 MongoDB
    """
    yield sse_event("thinking", {"status": "start", "phase": "initializing"})

    # 每个用户有独立 Agent（含 Proxy、中间件缓存和 SandboxManager 映射）。
    session = await agent_loader.get_session(user_id, username)
    config = traced_config(agent_loader.create_config(thread_id, user_id))
    if resume_data is not None:
        decisions = resume_data.get('decisions', []) if isinstance(resume_data, dict) else []
        event('hitl.resume', approved=any(d.get('type') in ('approve','edit') for d in decisions if isinstance(d,dict)),
              rejected=any(d.get('type')=='reject' for d in decisions if isinstance(d,dict)))

    # 加载已有消息（恢复场景）或初始化
    if resume_data:
        display_messages = await agent_loader.get_display_messages(thread_id)
        decisions = resume_data.get("decisions", []) if isinstance(resume_data, dict) else []
        # edit 同样表示人已确认，只是提交前改了参数；否则会被当成"未授权"阻断写操作
        approved = any(
            isinstance(d, dict) and d.get("type") in ("approve", "edit")
            for d in decisions
        )
        # 前端的 edit 只带散参数，这里补成框架要求的 edited_action 再交给图
        resume_value = resume_data
        if isinstance(resume_data, dict) and isinstance(resume_data.get("decisions"), list):
            resume_value = {
                **resume_data,
                "decisions": normalize_edit_decisions(resume_data["decisions"]),
            }
        current_input = Command(
            resume=resume_value,
            update={"_human_approved": approved},
        )
    else:
        display_messages = await agent_loader.get_display_messages(thread_id)
        display_messages.append({
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })
        current_input = {"messages": [{"role": "user", "content": message}]}

    # 当前助手消息累积缓冲
    assistant_content = ""
    tool_calls_buffer = []
    # 工具调用之后模型会开一段新文本，两段之间没有分隔符就会连成
    # "……定位零件。✅ 已获取数据" 这种一句话。这里记一笔，续写前补空行。
    pending_separator = False
    thinking_emitted = False
    phase = "thinking"
    last_phase = "thinking"
    last_todos_sig = None
    last_review_sig = None
    harness_trace: list = []

    from ...agent.backends.sandbox_holder import bind_sandbox, reset_sandbox
    from ...agent.backends.sandbox_manager import sandbox_manager
    from ...agent.runtime_context import bind_runtime, reset_runtime

    import time
    lock_started = time.perf_counter()
    await session.run_lock.acquire()
    event('request.lock_wait', duration_ms=(time.perf_counter()-lock_started)*1000)
    lease_acquired = sandbox_manager.acquire_lease(user_id)
    sandbox_token = bind_sandbox(user_id)
    runtime_tokens = bind_runtime(user_id, agent_loader.store)

    try:
        yield sse_event("thinking", {"status": "start"})

        async for namespace, chunk_type, chunk in session.agent.astream(
            input=current_input,
            config=config,
            stream_mode=["messages", "values", "custom"],
            subgraphs=True,
        ):
            # ===== 中断检测 + 结构化 Harness 状态读取（必须在 messages 处理之前）=====
            if chunk_type == "values":
                if isinstance(chunk, dict):
                    if not namespace and chunk.get("review_decision"):
                        decision = chunk["review_decision"]
                        event('review.decision', review=decision.get('decision')=='review')
                        signature = json.dumps(decision, ensure_ascii=False, sort_keys=True)
                        if signature != last_review_sig:
                            last_review_sig = signature
                            harness_trace.append({"type": "review_decision", **decision,
                                                  "timestamp": datetime.now().isoformat()})
                    # --- 结构化阶段流转（来自 HarnessPhaseMiddleware 写入的 state.phase）---
                    new_phase = chunk.get("phase")
                    if new_phase and new_phase != last_phase:
                        last_phase = new_phase
                        phase = new_phase
                        label = PHASE_LABELS.get(new_phase, new_phase)
                        harness_trace.append({
                            "type": "phase",
                            "phase": new_phase,
                            "label": label,
                            "timestamp": datetime.now().isoformat(),
                        })
                        yield sse_event("phase", {
                            "phase": new_phase,
                            "label": label,
                        })

                    # --- 结构化 todo 列表（来自 TodoListMiddleware 的 write_todos 状态）---
                    todos = chunk.get("todos")
                    if todos is not None:
                        todos_sig = json.dumps(todos, ensure_ascii=False, sort_keys=True)
                        if todos_sig != last_todos_sig:
                            last_todos_sig = todos_sig
                            plan_items = [
                                {
                                    "id": f"todo-{i}",
                                    "content": t.get("content", ""),
                                    "status": t.get("status", "pending"),
                                }
                                for i, t in enumerate(todos)
                            ]
                            if plan_items:
                                yield sse_event("todo_update", {
                                    "phase": "planning",
                                    "todos": plan_items,
                                })

                    # --- 中断检测 ---
                    # LangGraph emits __interrupt__; retain the legacy state key.
                    interrupts = chunk.get("__interrupt__") or chunk.get("interrupts")
                    if interrupts:
                        for interrupt_item in interrupts:
                            interrupt_value = (
                                interrupt_item.value
                                if hasattr(interrupt_item, "value")
                                else interrupt_item
                            )
                            if isinstance(interrupt_value, dict):
                                # 判断中断类型
                                if interrupt_value.get("type") == "order_info_request":
                                    yield sse_event("interrupt", {
                                        "interrupt_type": "order_info_supplement",
                                        "missing_fields": interrupt_value.get("missing_fields", []),
                                        "message": interrupt_value.get("message", ""),
                                        "extracted_data": interrupt_value.get("current_data", {}),
                                    })
                                elif interrupt_value.get("action_requests"):
                                    payload = await enrich_status_change(
                                        flatten_hitl_actions(interrupt_value)
                                    )
                                    yield sse_event("interrupt", {
                                        "interrupt_type": "hitl_approval",
                                        **payload,
                                    })
                                else:
                                    # 通用中断（interrupt_on 触发的审批）
                                    yield sse_event("interrupt", {
                                        "interrupt_type": "hitl_approval",
                                        "tool_name": interrupt_value.get("tool_name", ""),
                                        "tool_args": interrupt_value.get("tool_args", interrupt_value),
                                        "order_data": interrupt_value.get("tool_args", interrupt_value),
                                    })

                        # 保存当前累积的消息
                        if assistant_content:
                            display_messages.append({
                                "id": str(uuid.uuid4()),
                                "role": "assistant",
                                "content": assistant_content,
                                "toolCalls": tool_calls_buffer,
                                "timestamp": datetime.now().isoformat(),
                            })
                        await agent_loader.save_display_messages(thread_id, display_messages)
                        await agent_loader.save_harness_trace(thread_id, harness_trace)
                        yield sse_event("done", {"thread_id": thread_id, "interrupted": True})
                        return
                continue

            # ===== Custom 流处理（评审器等中间件的结构化事件）=====
            if chunk_type == "custom":
                if isinstance(chunk, dict):
                    ev_type = chunk.get("type", "")
                    if ev_type == "rubric_evaluation_start":
                        event('grader.start')
                        # 评审器开始 grading → 进入审查阶段
                        if phase != "reviewing":
                            phase = "reviewing"
                            last_phase = "reviewing"
                            yield sse_event("phase", {"phase": "reviewing", "label": "🔍 审查中"})
                    elif ev_type == "rubric_evaluation_end":
                        event('grader.end', satisfied=chunk.get('result')=='satisfied', iteration=chunk.get('iteration',0))
                        # 评审器产出结构化判定 → 发射 review_result 事件
                        review_data = {
                            "verdict": chunk.get("result", ""),
                            "explanation": chunk.get("explanation", ""),
                            "criteria": chunk.get("criteria", []),
                            "iteration": chunk.get("iteration", 0),
                        }
                        harness_trace.append({
                            "type": "review",
                            **review_data,
                            "timestamp": datetime.now().isoformat(),
                        })
                        yield sse_event("review_result", review_data)
                continue

            # ===== Messages 流处理 =====
            if chunk_type == "messages":
                # Routing output is internal control data, never assistant text
                # or a user-visible tool invocation.
                metadata = chunk[1] if isinstance(chunk, (tuple, list)) and len(chunk) > 1 else {}
                if isinstance(metadata, dict) and "review_router" in (metadata.get("tags") or []):
                    continue
                # chunk 格式: (message_chunk, metadata_dict)
                if isinstance(chunk, (list, tuple)) and len(chunk) >= 1:
                    token = chunk[0]
                else:
                    token = chunk

                if token is None:
                    continue

                token_type = getattr(token, "type", "")
                content = getattr(token, "content", "")
                tool_call_chunks = getattr(token, "tool_call_chunks", None)

                # AIMessage with tool_call_chunks → tool_start / tool_args
                if tool_call_chunks:
                    # 首次收到工具调用时结束 thinking 状态
                    if not thinking_emitted:
                        yield sse_event("thinking", {"status": "end"})
                        thinking_emitted = True
                    for tc in tool_call_chunks:
                        if isinstance(tc, dict):
                            if tc.get("name"):
                                tool_calls_buffer.append({
                                    "id": tc.get("id", str(uuid.uuid4())),
                                    "name": tc["name"],
                                    "args": "",
                                    "status": "running",
                                })
                                yield sse_event("tool_start", {
                                    "name": tc["name"],
                                    "id": tc.get("id", ""),
                                })
                            if tc.get("args"):
                                # 追加到最近的 tool_call
                                if tool_calls_buffer:
                                    tool_calls_buffer[-1]["args"] += tc["args"]
                                yield sse_event("tool_args", {"args": tc["args"]})
                    continue

                # ToolMessage → tool_result / tool_end
                if token_type == "tool":
                    tool_name = getattr(token, "name", "unknown")
                    tool_content = content if isinstance(content, str) else str(content)
                    # 更新 buffer 中对应工具的状态
                    for tc in tool_calls_buffer:
                        if tc["name"] == tool_name and tc["status"] == "running":
                            tc["result"] = tool_content[:1000]
                            tc["status"] = "done"
                            break

                    # 检测 TODO 工具调用，发射任务列表更新事件
                    if "todo" in tool_name.lower() or "task" in tool_name.lower():
                        try:
                            # 尝试从工具参数中提取 todo 列表
                            todo_args = None
                            for tc in tool_calls_buffer:
                                if tc["name"] == tool_name and tc.get("args"):
                                    todo_args = tc["args"]
                                    break
                            if todo_args:
                                yield sse_event("todo_update", {
                                    "tool": tool_name,
                                    "args": todo_args[:2000],
                                    "result": tool_content[:500],
                                })
                        except Exception:
                            pass

                    yield sse_event("tool_result", {
                        "name": tool_name,
                        "content": tool_content[:2000],
                    })
                    yield sse_event("tool_end", {
                        "id": getattr(token, "tool_call_id", ""),
                    })
                    # 工具跑完，模型接下来这段是新的一段话
                    pending_separator = True
                    continue

                # AIMessage 纯文本 → token 事件
                # 只把模型（AI）的文本当作回答；跳过 Human/System 注入消息
                # （例如 rubric 评审器注入的英文修订反馈，绝不能展示给用户）。
                src_name = getattr(token, "name", None)
                extra = getattr(token, "additional_kwargs", {}) or {}
                is_internal = (
                    src_name == "rubric_grader"
                    or extra.get("lc_source") == "rubric_grader"
                )
                is_ai_text = token_type in ("ai", "AIMessageChunk")

                # 纯空白分片必须原样透传：流式输出时 "\n\n" 经常单独成一个 chunk，
                # 以前用 content.strip() 过滤会把它整块丢掉，导致入库和前端都出现
                # 标题粘连正文、列表项挤成一行、表格贴着上一段（历史消息同样受损）。
                if (
                    is_ai_text
                    and not is_internal
                    and isinstance(content, str)
                    and content != ""
                ):
                    # 只有出现可见字符才结束 thinking，纯换行不应让"思考中"提前消失
                    if content.strip() and not thinking_emitted:
                        yield sse_event("thinking", {"status": "end"})
                        thinking_emitted = True
                    if pending_separator and content.strip():
                        # 上一段是工具调用前的说明文字，这里补空行另起一段，
                        # 否则会出现"……定位零件。✅ 已获取数据"这种粘连
                        if assistant_content and not assistant_content.endswith("\n") \
                                and not content.startswith("\n"):
                            content = "\n\n" + content
                        pending_separator = False
                    assistant_content += content

                    # 判断来源（通过 namespace 元组）
                    source = "main"
                    if namespace:
                        ns_str = str(namespace)
                        if "analyst" in ns_str:
                            source = "analyst"
                        elif "order" in ns_str:
                            source = "order"
                    yield sse_event("token", {"content": content, "source": source})

        # ===== 流正常结束 =====
        if assistant_content:
            display_messages.append({
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": assistant_content,
                "toolCalls": tool_calls_buffer if tool_calls_buffer else None,
                "timestamp": datetime.now().isoformat(),
            })

        await agent_loader.save_display_messages(thread_id, display_messages)
        await agent_loader.save_harness_trace(thread_id, harness_trace)

        # 自动生成会话标题（首条消息前20字）
        if message:
            title = message[:20] + "..." if len(message) > 20 else message
            await agent_loader.save_conversation(thread_id, user_id, title)

        yield sse_event("done", {"thread_id": thread_id, "interrupted": False})

    except Exception as e:
        web_logger.error(f"Stream error for thread {thread_id}: {e}", exc_info=True)
        yield sse_event("error", {"message": f"服务内部错误: {str(e)[:200]}"})
        yield sse_event("done", {"thread_id": thread_id, "interrupted": False})
    finally:
        reset_runtime(runtime_tokens)
        reset_sandbox(sandbox_token)
        if lease_acquired:
            sandbox_manager.release_lease(user_id)
        session.run_lock.release()


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式对话端点"""
    await agent_loader.initialize()

    thread_id = request.thread_id or agent_loader.generate_thread_id()
    existing_owner = await agent_loader.get_conversation_user_id(thread_id)
    if existing_owner and existing_owner != request.user_id:
        raise HTTPException(status_code=403, detail="该会话不属于当前用户")

    # 在流开始时就记录归属，确保在中断后 resume 能找到同一个用户沙箱。
    title = request.message[:20] + "..." if len(request.message) > 20 else request.message
    await agent_loader.save_conversation(thread_id, request.user_id, title)

    return StreamingResponse(
        stream_chat_response(
            message=request.message,
            thread_id=thread_id,
            user_id=request.user_id,
            username=request.username,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Thread-Id": thread_id,
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{thread_id}/resume")
async def chat_resume(thread_id: str, request: ResumeRequest):
    """中断恢复端点"""
    await agent_loader.initialize()
    user_id = await agent_loader.get_conversation_user_id(thread_id) or "default_user"

    return StreamingResponse(
        stream_chat_response(
            message="",
            thread_id=thread_id,
            user_id=user_id,
            resume_data=request.resume,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Thread-Id": thread_id,
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{thread_id}/state")
async def chat_state(thread_id: str):
    """获取 Agent 状态（是否处于中断中）"""
    await agent_loader.initialize()
    user_id = await agent_loader.get_conversation_user_id(thread_id) or "default_user"
    session = await agent_loader.get_session(user_id)
    config = agent_loader.create_config(thread_id, user_id)
    try:
        state = await session.agent.aget_state(config)
        is_interrupted = bool(state.next) if state else False
        return {"thread_id": thread_id, "interrupted": is_interrupted}
    except Exception:
        return {"thread_id": thread_id, "interrupted": False}


@router.get("/{thread_id}/history")
async def chat_history(thread_id: str):
    """获取对话消息历史"""
    messages = await agent_loader.get_display_messages(thread_id)
    return {"thread_id": thread_id, "messages": messages}
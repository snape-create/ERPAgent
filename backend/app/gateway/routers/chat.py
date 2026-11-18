"""对话路由：只做 HTTP 语义（校验、归属、响应装配）。

流式编排在 ``app.services.chat_stream``，事件整形在 ``app.services.chat_turn``，
HITL 载荷在 ``app.services.hitl``。
"""
import uuid

from fastapi import APIRouter, HTTPException

from app.repositories.conversations import (
    get_conversation_user_id,
    get_display_messages,
    save_conversation,
)
from app.services.chat_stream import stream_chat_response
from app.services.chat_turn import conversation_title
from app.services.hitl import build_interrupt_event, enrich_status_change
from app.services.langgraph_adapter import unwrap_interrupt
from app.services.langgraph_client import get_client, server_thread_id
from app.services.sse import sse_response
from erpflow.schema import ChatRequest, ResumeRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """SSE 流式对话端点"""
    thread_id = request.thread_id or str(uuid.uuid4())
    existing_owner = await get_conversation_user_id(thread_id)
    if existing_owner and existing_owner != request.user_id:
        raise HTTPException(status_code=403, detail="该会话不属于当前用户")

    # 在流开始时就记录归属，确保中断后 resume 能找回同一个用户。
    await save_conversation(
        thread_id, request.user_id, conversation_title(request.message)
    )

    return sse_response(
        stream_chat_response(
            message=request.message,
            thread_id=thread_id,
            user_id=request.user_id,
            username=request.username,
        ),
        thread_id,
    )


@router.post("/{thread_id}/resume")
async def chat_resume(thread_id: str, request: ResumeRequest):
    """中断恢复端点"""
    # 会话归属查不到就是真的不存在（历史数据缺失 / 已被删除 / thread_id 是前端本地生成的）。
    # 不回落兜底身份：那会凭空造出一个查不到归属的会话，还把错误吞掉。
    user_id = await get_conversation_user_id(thread_id)
    if not user_id:
        raise HTTPException(status_code=404, detail="会话不存在或已删除，无法恢复")

    return sse_response(
        stream_chat_response(
            message="",
            thread_id=thread_id,
            user_id=user_id,
            resume_data=request.resume,
        ),
        thread_id,
    )


@router.get("/{thread_id}/state")
async def chat_state(thread_id: str):
    """获取 Agent 状态（是否处于中断中）"""
    user_id = await get_conversation_user_id(thread_id)
    if not user_id:
        raise HTTPException(status_code=404, detail="会话不存在或已删除")

    try:
        # SDK 返回 ThreadState（dict）：next / tasks[*].interrupts 全部按键访问。
        # 服务端 thread id 必须是 UUID，查询同样要过映射。
        state = await get_client().threads.get_state(server_thread_id(thread_id))
        is_interrupted = bool(state.get("next")) if state else False
        payload = await _pending_interrupt(state) if is_interrupted else None

        # 刷新页面后前端拿不到 SSE 里的 interrupt 事件，这里把待审批内容一并返回，
        # 否则用户只能看到一个"没有卡片的空白会话"。
        return {
            "thread_id": thread_id,
            "interrupted": is_interrupted or payload is not None,
            "interrupt": payload,
        }
    except Exception:
        return {"thread_id": thread_id, "interrupted": False, "interrupt": None}


@router.get("/{thread_id}/history")
async def chat_history(thread_id: str):
    """获取对话消息历史"""
    messages = await get_display_messages(thread_id)
    return {"thread_id": thread_id, "messages": messages}


async def _pending_interrupt(state: dict) -> dict | None:
    """从 ThreadState 里取出待处理的中断，转成前端卡片载荷（取第一个）。

    tasks / interrupts 都是 dict，中断项还要先解包。
    """
    for task in state.get("tasks") or ():
        if not isinstance(task, dict):
            continue
        for item in task.get("interrupts") or ():
            value = unwrap_interrupt(item)
            if not isinstance(value, dict):
                continue
            payload = build_interrupt_event(value)
            if payload.get("interrupt_type") == "hitl_approval":
                payload = await enrich_status_change(payload)
            return payload
    return None

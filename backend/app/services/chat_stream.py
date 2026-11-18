"""对话流式编排：输入构造 + 事件循环 + 兜底持久化。

分工：
- ``ChatTurn``（chat_turn.py）  负责「chunk → 事件」
- ``hitl``（hitl.py）           负责「中断 ↔ 卡片」形状转换
- ``sse``（sse.py）             负责线格式
- 本模块                         负责把上面几件事串成一次请求的生命周期

读流统一走 ``langgraph_sdk.runs.stream``：StreamPart 先经 ``langgraph_adapter``
抹平差异，再交给同一个 ``ChatTurn``，事件名/顺序/字段由 ``ChatTurn`` 决定。
"""
import asyncio
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable
from datetime import datetime
from functools import partial

from app.repositories.conversations import (
    get_display_messages,
    save_display_messages,
)
from app.services.chat_turn import ChatTurn
from app.services.hitl import is_approved, normalize_edit_decisions
from app.services.langgraph_adapter import adapt_stream_part, split_event
from app.services.langgraph_client import get_client, server_thread_id
from app.services.sse import sse_event
from erpflow.integrations.erp.approval import resolve_part_codes
from erpflow.log_utils import web_logger

# 客户端断流时最后一次落库的最长等待（秒）
FINAL_PERSIST_TIMEOUT_SECONDS = 3

# LangGraph Server 侧注册的图名与需要的流模式（messages-tuple 由服务端改写为 messages）
ASSISTANT_ID = "lead_agent"
STREAM_MODES = ["messages-tuple", "values", "custom"]

Event = tuple[str, dict]
StreamSource = AsyncIterator[tuple[list[str], str, object]]


async def _build_resume_payload(resume_data: dict) -> dict:
    """把前端 resume 载荷翻译成 SDK ``Command`` 的 dict 形状。

    - edit 决策补成框架要求的 ``edited_action``；
    - 补充信息里的零件编码换成 partId；
    - 只有带 decisions 时才写 ``_human_approved``：中断点的待应用写入已带该键，
      同一 super-step 再写一次会抛
      ``InvalidUpdateError: Can receive only one value per step``。
    """
    decisions = resume_data.get("decisions", []) if isinstance(resume_data, dict) else []
    resume_value = resume_data

    if isinstance(resume_data, dict) and isinstance(resume_data.get("decisions"), list):
        resume_value = {
            **resume_data,
            "decisions": normalize_edit_decisions(resume_data["decisions"]),
        }

    supplement = resume_value.get("supplement") if isinstance(resume_value, dict) else None
    if isinstance(supplement, str):
        resume_value = {**resume_value, "supplement": await resolve_part_codes(supplement)}

    payload: dict = {"resume": resume_value}
    if decisions:
        payload["update"] = {"_human_approved": is_approved(decisions)}
    return payload


async def _prepare_turn(
    message: str,
    thread_id: str,
    resume_data: dict | None,
) -> tuple[list, dict | None]:
    """读取展示消息并判定本轮输入。

    Returns:
        ``(display_messages, resume_payload)``。新消息返回 ``None`` 作为
        resume 载荷，并在此刻落库（随后断流也不会丢掉用户这句话）。
    """
    display_messages = await get_display_messages(thread_id)
    if resume_data:
        return display_messages, await _build_resume_payload(resume_data)

    display_messages.append({
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": message,
        "timestamp": datetime.now().isoformat(),
    })
    await save_display_messages(thread_id, display_messages)
    return display_messages, None


async def _dispatch_chunk(
    turn: ChatTurn,
    namespace: list[str],
    chunk_type: str,
    chunk: object,
) -> tuple[list[Event], bool]:
    """把一条原始流数据交给 ``ChatTurn``，返回 (事件列表, 是否中断)。"""
    if chunk_type == "values":
        return await turn.handle_values(namespace, chunk)
    if chunk_type == "custom":
        return await turn.handle_custom(chunk), False
    if chunk_type in ("messages", "messages-tuple"):
        return await turn.handle_message(namespace, chunk), False
    return [], False


async def _stream_turn(
    turn: ChatTurn,
    source: StreamSource,
    thread_id: str,
    on_complete: Callable[[], Awaitable[None]],
) -> AsyncGenerator[str]:
    """消费统一的 ``(namespace, mode, chunk)`` 流并产出 SSE 文本。

    事件的派生与顺序只在这一处；``done`` 仍由网关合成（中断 → interrupted=True，
    正常结束 → False）。
    """
    async for namespace, chunk_type, chunk in source:
        events, interrupted = await _dispatch_chunk(turn, namespace, chunk_type, chunk)
        for name, data in events:
            yield sse_event(name, data)
        if interrupted:
            await turn.finish_interrupted()
            yield sse_event("done", {"thread_id": thread_id, "interrupted": True})
            return

    await on_complete()
    yield sse_event("done", {"thread_id": thread_id, "interrupted": False})


async def _adapt_server_stream(stream: AsyncIterator) -> AsyncGenerator[tuple[list[str], str, object]]:
    """把 SDK ``StreamPart`` 流适配成 ``ChatTurn`` 的输入三元组。

    服务端 ``error`` 事件只取 ``message``，抛给外层统一走现有 error 事件壳；
    其余控制事件（metadata/end）由适配层丢弃。
    """
    async for part in stream:
        mode, _ = split_event(part.event)
        if mode == "error":
            raise RuntimeError(_server_error_message(part.data))
        adapted = adapt_stream_part(part.event, part.data)
        if adapted is not None:
            yield adapted


def _server_error_message(data) -> str:
    """服务端 error 载荷形如 ``{"error": 类型名, "message": ...}``，只取 message。"""
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return str(data)


def _server_config(thread_id: str, user_id: str, username: str) -> dict:
    """Server 侧图工厂只读 configurable；用户身份必须在这里，不能走 context。

    ``thread_id`` 传映射后的 UUID：服务端 checkpoint 与 configurable 必须是同一个
    身份，否则中间件按 thread 隔离的上下文对不上。
    """
    return {
        "configurable": {
            "thread_id": thread_id,
            "user_id": user_id,
            "username": username,
        }
    }


async def _final_persist(turn: ChatTurn, display_messages: Iterable, thread_id: str) -> None:
    """断流兜底：shield 最后一次写入，超时或取消不阻塞收尾。"""
    if not display_messages:
        return
    try:
        await asyncio.wait_for(
            asyncio.shield(turn.persist()), timeout=FINAL_PERSIST_TIMEOUT_SECONDS
        )
    except (TimeoutError, asyncio.CancelledError):
        pass
    except Exception as exc:
        web_logger.warning(f"Failed to persist progress for {thread_id}: {exc}")


async def stream_chat_response(
    message: str,
    thread_id: str,
    user_id: str = "default_user",
    username: str = "用户",
    resume_data: dict = None,
) -> AsyncGenerator[str]:
    """SSE 事件生成器：创建 thread → 消费 SDK 流 → 统一收尾。

    服务端只接受 UUID 作为 thread id，SDK 侧统一用 ``server_thread_id`` 映射；
    展示消息、SSE 事件与库里的会话仍用原始 ``thread_id``。

    刻意不先 yield：用户消息要先落库再开始推事件，
    否则客户端在第一个事件后就断开时，这句话会彻底丢失。
    """
    client = get_client()
    sdk_thread_id = server_thread_id(thread_id)
    display_messages, resume_payload = await _prepare_turn(message, thread_id, resume_data)
    turn = ChatTurn(thread_id, display_messages)

    yield sse_event("thinking", {"status": "start", "phase": "initializing"})

    try:
        # 历史 thread 只存在网关 Mongo 里，服务端没有对应 thread 时 run 会被拒
        await client.threads.create(thread_id=sdk_thread_id, if_exists="do_nothing")

        yield sse_event("thinking", {"status": "start"})

        run_args: dict = (
            {"command": resume_payload}
            if resume_payload is not None
            else {"input": {"messages": [{"role": "user", "content": message}]}}
        )
        stream = client.runs.stream(
            sdk_thread_id,
            ASSISTANT_ID,
            config=_server_config(sdk_thread_id, user_id, username),
            stream_mode=STREAM_MODES,
            stream_subgraphs=True,
            **run_args,
        )

        try:
            async for event in _stream_turn(
                turn,
                _adapt_server_stream(stream),
                thread_id,
                on_complete=partial(turn.finish_completed, user_id, message),
            ):
                yield event
        finally:
            # 中断分支会提前 return / 客户端断流会取消生成器；
            # 不关掉流会让服务端 run 一直挂着。
            await stream.aclose()

    except Exception as exc:
        web_logger.error(f"Stream error for thread {thread_id}: {exc}", exc_info=True)
        yield sse_event("error", {"message": f"服务内部错误: {str(exc)[:200]}"})
        yield sse_event("done", {"thread_id": thread_id, "interrupted": False})
    finally:
        await _final_persist(turn, display_messages, thread_id)

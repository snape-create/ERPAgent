"""SSE 协议封装：事件序列化 + StreamingResponse 装配。

前端契约只有这一处：``event: <name>\\ndata: <json>\\n\\n``，
其余模块只关心「发什么事件」，不关心线格式。
"""
import json
from collections.abc import AsyncGenerator

from fastapi.responses import StreamingResponse

# 流式过程中保存进度的最小间隔（秒）；断流时还会额外做一次兜底保存
PERSIST_INTERVAL_SECONDS = 1.5

# 关闭代理/网关缓冲，保证逐字下发
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_event(event: str, data: dict) -> str:
    """格式化单个 SSE 事件（严格遵循 event:/data: 协议）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_response(
    generator: AsyncGenerator[str],
    thread_id: str,
) -> StreamingResponse:
    """把事件生成器包成 text/event-stream 响应，并回传 thread_id 供前端续用。"""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Thread-Id": thread_id},
    )

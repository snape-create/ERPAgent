"""历史会话管理：会话列表 / 消息查询 / 会话删除。

只有 HTTP 语义；读写都在 ``app.repositories.conversations``。
"""
from fastapi import APIRouter, Query

from app.repositories import conversations as repo

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def list_conversations(user_id: str = Query(default="default_user")):
    """获取会话列表"""
    return {"conversations": await repo.get_conversations(user_id)}


@router.get("/{thread_id}/messages")
async def get_messages(thread_id: str):
    """获取指定会话的消息列表"""
    return {"thread_id": thread_id, "messages": await repo.get_display_messages(thread_id)}


@router.delete("/{thread_id}")
async def delete_conversation(thread_id: str):
    """删除会话（硬删除，含消息与追踪）"""
    await repo.delete_conversation(thread_id)
    return {"success": True, "message": f"会话 {thread_id} 已删除"}

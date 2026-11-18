"""会话相关的 MongoDB 读写。

三个集合同属「一次会话」这个聚合：
    conversations     会话元数据（归属、标题、时间）
    display_messages  前端展示消息（用户可见的对话记录）
    harness_traces    阶段流转与评审判定（可观测/审计）

这里只做数据访问，不含任何 Agent 生命周期或 HTTP 语义。
"""
from datetime import datetime

from app.repositories.mongo import get_db


async def save_conversation(thread_id: str, user_id: str, title: str = "新对话") -> None:
    """写入/更新会话元数据（upsert，created_at 只在首次插入时写）。"""
    db = get_db()
    now = datetime.now().isoformat()
    await db.conversations.update_one(
        {"thread_id": thread_id},
        {"$set": {"user_id": user_id, "title": title, "updated_at": now},
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


async def get_conversations(user_id: str) -> list:
    """按最近更新时间倒序列出用户的会话。"""
    db = get_db()
    cursor = db.conversations.find({"user_id": user_id}).sort("updated_at", -1)
    conversations = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        conversations.append(doc)
    return conversations


async def get_conversation_user_id(thread_id: str) -> str | None:
    """查询会话归属，供 resume/state 选择正确的用户 Agent。"""
    db = get_db()
    doc = await db.conversations.find_one({"thread_id": thread_id}, {"user_id": 1})
    return doc.get("user_id") if doc else None


async def delete_conversation(thread_id: str) -> None:
    """硬删除会话及其消息、追踪（不可恢复）。"""
    db = get_db()
    await db.conversations.delete_one({"thread_id": thread_id})
    await db.display_messages.delete_one({"thread_id": thread_id})
    await db.harness_traces.delete_one({"thread_id": thread_id})


async def save_display_messages(thread_id: str, messages: list) -> None:
    """整体覆盖保存前端展示消息（流式过程中会被节流调用）。"""
    db = get_db()
    await db.display_messages.update_one(
        {"thread_id": thread_id},
        {"$set": {"messages": messages, "updated_at": datetime.now().isoformat()}},
        upsert=True,
    )


async def get_display_messages(thread_id: str) -> list:
    """读取前端展示消息；没有记录时返回空列表。"""
    db = get_db()
    doc = await db.display_messages.find_one({"thread_id": thread_id})
    return doc.get("messages", []) if doc else []


async def save_harness_trace(thread_id: str, trace: list) -> None:
    """保存 Harness 阶段流转 trace。

    记录 Planning → Executing → Review → Result 的时间线，
    以及评审器的结构化判定结果，支持事后审计「哪一步卡住了」。
    """
    db = get_db()
    await db.harness_traces.update_one(
        {"thread_id": thread_id},
        {"$set": {"trace": trace, "updated_at": datetime.now().isoformat()}},
        upsert=True,
    )

"""Checkpointer 工厂（LangGraph Server 入口）。

``langgraph.json`` 的 ``checkpointer.path`` 指向本模块的 ``make_checkpointer``，
server 启动时按文件路径加载（不是按包导入），所以**这里必须用绝对 import**：
相对 import 会报 "attempted relative import with no known parent package"。

Mongo 不可用时回退到内存 saver，保证 ``uv run langgraph dev`` 仍能起进程
（会话不持久化，但不会启动即崩）。
"""
from __future__ import annotations

import threading

from erpflow.log_utils import agent_logger

_checkpointer = None
_lock = threading.Lock()


def make_checkpointer():
    """返回进程级 checkpointer 单例（优先 MongoDB，失败回退 InMemorySaver）。"""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    with _lock:
        if _checkpointer is not None:
            return _checkpointer

        _checkpointer = _build_mongo_checkpointer() or _build_memory_checkpointer()
        return _checkpointer


def _build_mongo_checkpointer():
    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver
        from pymongo import MongoClient

        from erpflow.config import MONGODB_URI

        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        # 立刻打一次 ping，把"配了 Mongo 但连不上"提前暴露成回退，而不是首次对话才 500
        client.admin.command("ping")
        agent_logger.info("Checkpointer: MongoDB")
        return MongoDBSaver(client)
    except Exception as exc:
        agent_logger.warning(
            f"Mongo checkpointer unavailable ({exc}); falling back to InMemorySaver"
        )
        return None


def _build_memory_checkpointer():
    from langgraph.checkpoint.memory import InMemorySaver

    agent_logger.info("Checkpointer: InMemorySaver（会话不跨重启保留）")
    return InMemorySaver()


def reset_checkpointer() -> None:
    """测试用：清掉单例。"""
    global _checkpointer
    _checkpointer = None

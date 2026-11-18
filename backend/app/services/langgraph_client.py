"""LangGraph Server 客户端：懒加载、进程内复用。

只在 ``AGENT_BACKEND=server`` 时被调用。首次使用时按 ``LANGGRAPH_URL`` 建客户端，
之后同一进程共用；import 期不建连接，也不新建事件循环。
"""
import uuid

from langgraph_sdk import get_client as get_sdk_client

from app.gateway.config import get_gateway_config

_client = None


def server_thread_id(thread_id: str) -> str:
    """应用侧任意字符串 → 稳定的服务端 UUID（同输入永远同输出）。

    LangGraph Server 只接受 UUID 作为 thread id；用 uuid5 做确定性映射，
    进程重启后同一会话仍命中同一 thread。Mongo 与对外响应继续用原始 id。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"erp-agent:{thread_id}"))


def get_client():
    """返回进程级复用的 LangGraph SDK 客户端（首次调用时创建）。"""
    global _client
    if _client is None:
        _client = get_sdk_client(url=get_gateway_config().langgraph_url)
    return _client

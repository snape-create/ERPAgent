"""LangGraph Server 图入口。

``langgraph.json`` 声明：

    "graphs": {"lead_agent": "erpflow.graph:make_lead_agent"}

server 以 ``RunnableConfig`` 调用本工厂，每个请求得到一张按用户装配好的图。
这里只做「配置解析」，装配逻辑仍在 ``agent.build.create_main_agent``，但必须
经由 :mod:`erpflow.agent.graph_cache`：Docker / 磁盘 / pymongo / MCP 都是阻塞
调用，不能直接跑在事件循环线程里（否则被 blockbuster 拦截）。

注意：checkpointer 由 server 按 ``langgraph.json`` 的 ``checkpointer.path`` 注入，
所以这里不再传 checkpointer；Store 则必须是进程级单例（server 不会注入）。
"""
from __future__ import annotations

from typing import Any

from .agent.graph_cache import ensure_sandbox_pool_once, get_or_build
from .schema import ProcurementContext


def make_lead_agent(config: dict[str, Any] | None = None):
    """LangGraph Server 的图工厂（命中缓存时不接触任何阻塞依赖）。"""
    configurable = (config or {}).get("configurable", {}) or {}
    user_context = ProcurementContext(
        user_id=configurable.get("user_id", "default_user"),
        username=configurable.get("username", "用户"),
    )
    return get_or_build(user_context)


__all__ = ["make_lead_agent", "ensure_sandbox_pool_once"]

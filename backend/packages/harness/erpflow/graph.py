"""LangGraph Server 图入口。

``langgraph.json`` 声明：

    "graphs": {"lead_agent": "erpflow.graph:make_lead_agent"}

server 以 ``RunnableConfig`` 调用本工厂，每个请求得到一张按用户装配好的图。
这里只做「配置解析 + 依赖准备」，装配逻辑仍在 ``agent.build.create_main_agent``。

注意：checkpointer 由 server 按 ``langgraph.json`` 的 ``checkpointer.path`` 注入，
所以这里不再传 checkpointer；Store 则必须是进程级单例（server 不会注入）。
"""
from __future__ import annotations

import threading
from typing import Any

from .agent import create_main_agent
from .log_utils import agent_logger
from .schema import ProcurementContext
from .store import get_store

# 预热池只补一次：server 进程内多次建图不应该反复创建容器
_pool_ready = False
_pool_lock = threading.Lock()


def ensure_sandbox_pool_once() -> None:
    """首次建图时补齐预热池，并起一个后台维护线程。"""
    global _pool_ready
    if _pool_ready:
        return
    with _pool_lock:
        if _pool_ready:
            return
        _pool_ready = True
        try:
            from .backends.pool_maintenance import start_maintenance

            start_maintenance()
        except Exception as exc:  # 预热失败不阻止建图，首个请求会按需创建
            agent_logger.warning(f"Sandbox pool bootstrap failed: {exc}")


def make_lead_agent(config: dict[str, Any] | None = None):
    """LangGraph Server 的图工厂。"""
    configurable = (config or {}).get("configurable", {}) or {}
    user_context = ProcurementContext(
        user_id=configurable.get("user_id", "default_user"),
        username=configurable.get("username", "用户"),
    )
    ensure_sandbox_pool_once()
    return create_main_agent(
        user_context=user_context,
        checkpointer=None,   # server 注入
        store=get_store(),
    )


__all__ = ["make_lead_agent", "ensure_sandbox_pool_once"]

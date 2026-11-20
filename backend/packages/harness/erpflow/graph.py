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

from langchain_core.runnables import RunnableConfig

from .agent.graph_cache import ensure_sandbox_pool_once, get_or_build
from .schema import ProcurementContext

# 非执行上下文（例如 LangGraph Server 读取 thread state）没有用户身份。
# 使用保留 ID 构建一张不带沙箱的只读图，避免误认领 default_user 的容器。
READONLY_USER_ID = "__readonly__"


def make_lead_agent(config: RunnableConfig | None = None):
    """LangGraph Server 的图工厂（命中缓存时不接触任何阻塞依赖）。

    只有真正执行 Agent 的调用才会带 ``configurable.user_id``。``threads.get_state``
    等非执行路径可能以空 config 调用本工厂；此时构建无沙箱图，绝不能用
    ``default_user`` 去领取/创建容器。
    """
    configurable = (config or {}).get("configurable", {}) or {}
    raw_user_id = configurable.get("user_id")
    user_id = str(raw_user_id).strip() if raw_user_id is not None else ""

    if not user_id:
        return get_or_build(
            ProcurementContext(user_id=READONLY_USER_ID, username=""),
            enable_sandbox=False,
        )

    user_context = ProcurementContext(
        user_id=user_id,
        username=configurable.get("username", "用户"),
    )
    return get_or_build(user_context)


__all__ = ["make_lead_agent", "ensure_sandbox_pool_once"]

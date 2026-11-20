"""按用户缓存主 Agent 图，并把所有阻塞装配挪出事件循环。

LangGraph Server 在事件循环线程里调用图工厂；``create_main_agent`` 会连 Docker、
跑 ``os.getcwd()``、起 MCP 子进程、连 pymongo，这些同步调用会被 blockbuster
直接拦截（``BlockingError``）。本模块把装配整体提交到专用线程池，事件循环只做
一次字典查找（命中）或等待 future（首次建图）。

同一用户的图终身复用：第二次对话不再建图，也就不再连 MCP / 重传沙箱文件。
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING

from ..log_utils import agent_logger
from ..schema import ProcurementContext

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

# 建图超时：首次要拉沙箱、上传项目、连 MCP，留足余量但避免永久挂起。
BUILD_TIMEOUT_SECONDS = 180
# 并发建图等待预热的上限；预热本身失败也会立刻放行，不会卡满这个时间。
WARMUP_WAIT_SECONDS = 180

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="graph-build")

_graphs: dict[str, CompiledStateGraph] = {}
_pending: dict[str, Future] = {}
_lock = threading.Lock()

# 进程级预热只跑一次：MCP 工具 / Store / 沙箱维护线程。
_warmup_lock = threading.Lock()
_warmup_started = False
_warmup_done = threading.Event()

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
            from ..backends.pool_maintenance import start_maintenance

            start_maintenance()
        except Exception as exc:  # 预热失败不阻止建图，首个请求会按需创建
            agent_logger.warning(f"Sandbox pool bootstrap failed: {exc}")


def _warm_mcp_tools() -> None:
    from ..tools.mcp_client import load_mcp_tools_sync

    tools = load_mcp_tools_sync()
    agent_logger.info(f"Graph cache warmup: {len(tools)} MCP tool(s) loaded")


def _warm_store() -> None:
    from ..store import get_store

    get_store()
    agent_logger.info("Graph cache warmup: store ready")


def _warmup() -> None:
    """在线程池里预热进程级依赖；任何一步失败都只告警。"""
    for name, step in (
        ("MCP tools", _warm_mcp_tools),
        ("store", _warm_store),
        ("sandbox pool", ensure_sandbox_pool_once),
    ):
        try:
            step()
        except Exception as exc:
            agent_logger.warning(f"Graph cache warmup step '{name}' failed: {exc}")


def _ensure_warmup() -> None:
    """保证预热只执行一次；并发建图等待同一次预热结束，避免重复连 MCP。"""
    global _warmup_started
    with _warmup_lock:
        leader = not _warmup_started
        _warmup_started = True

    if not leader:
        if not _warmup_done.is_set():
            _warmup_done.wait(timeout=WARMUP_WAIT_SECONDS)
        return

    try:
        _warmup()
    finally:
        _warmup_done.set()


def start_warmup() -> None:
    """在后台线程提前预热（供进程启动钩子调用）；重复调用无副作用。"""
    _executor.submit(_ensure_warmup)


def _build(user_context: ProcurementContext, enable_sandbox: bool = True):
    """在 executor 线程里完成全部阻塞装配。

    非执行上下文（state 读取）不预热沙箱池，也不领取用户容器。
    """
    if enable_sandbox:
        _ensure_warmup()

    from ..store import get_store
    from .build import create_main_agent

    return create_main_agent(
        user_context=user_context,
        checkpointer=None,   # server 注入
        store=get_store(),
        enable_sandbox=enable_sandbox,
    )


def _remember(cache_key: str, future: Future) -> None:
    """future 完成时落缓存/清 pending；超时后完成的图也能被后续请求复用。"""
    try:
        graph = future.result()
    except Exception as exc:
        with _lock:
            _pending.pop(cache_key, None)
        agent_logger.warning(f"Graph build failed for {cache_key}: {exc}")
        return
    with _lock:
        _graphs[cache_key] = graph
        _pending.pop(cache_key, None)
    agent_logger.info(f"Graph cached for {cache_key}")


def get_or_build(
    user_context: ProcurementContext,
    timeout: float = BUILD_TIMEOUT_SECONDS,
    *,
    enable_sandbox: bool = True,
):
    """返回该用户的图；未命中则在线程池里装配。

    ``enable_sandbox=False`` 用于 state 读取等非执行路径，缓存键与执行图分开。
    """
    user_id = user_context.user_id
    cache_key = user_id if enable_sandbox else f"__readonly__:{user_id}"

    with _lock:
        cached = _graphs.get(cache_key)
        if cached is not None:
            return cached
        future = _pending.get(cache_key)
        if future is None:
            future = _executor.submit(_build, user_context, enable_sandbox)
            future.add_done_callback(
                lambda fut, key=cache_key: _remember(key, fut)
            )
            _pending[cache_key] = future

    try:
        # 事件循环线程只等 future，不碰 Docker/磁盘/pymongo。
        return future.result(timeout=timeout)
    except TimeoutError:
        agent_logger.error(
            f"Graph build for {cache_key} timed out after {timeout}s"
        )
        raise


def invalidate_all() -> None:
    """丢弃所有用户的图（MCP 配置变更后调用），下次请求重新装配。"""
    with _lock:
        _graphs.clear()
    agent_logger.info("Graph cache invalidated")


__all__ = [
    "BUILD_TIMEOUT_SECONDS",
    "ensure_sandbox_pool_once",
    "get_or_build",
    "invalidate_all",
    "start_warmup",
]

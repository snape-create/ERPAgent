"""
MCP 工具加载模块
通过环境变量 MCP_USE_EMBEDDED=false 可切回旧的 SSE 模式（连接独立 MCP Server）。

生产级特性：
- 连接/加载重试（指数退避）
- 结果缓存
- 优雅降级（加载失败时返回空工具列表，Agent 仍可对话）
"""
import asyncio
import time
from typing import Any, List, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from ..config import MCP_TRANSPORT, MCP_URL, MCP_USE_EMBEDDED
from ..log_utils import mcp_logger

# 重试配置
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # 秒
RETRY_BACKOFF_FACTOR = 2.0

# 缓存（避免重复加载）
_cached_tools: List[BaseTool] | None = None
_cache_time: float = 0
CACHE_TTL = 300  # 5分钟缓存


def _json_type_to_python(prop: dict) -> Any:
    """把 JSON Schema 的 type 映射成 Python 类型。"""
    t = prop.get("type")
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        items = prop.get("items", {})
        inner = _json_type_to_python(items) if isinstance(items, dict) else Any
        return list[inner]
    if t == "object":
        return dict
    return Any


def _build_args_model(name: str, parameters: dict):
    """根据 FastMCP 解析出的 JSON Schema 构造 Pydantic 参数模型。

    保留每个字段的 description 与 default，确保 LLM 拿到和 MCP 协议一致的
    参数说明（否则参数描述会丢失）。
    """
    props = parameters.get("properties") or {}
    if not props:
        return None

    fields = {}
    for pname, prop in props.items():
        py_type = _json_type_to_python(prop)
        description = prop.get("description")
        if "default" in prop:
            default = prop.get("default")
            if default is None:
                fields[pname] = (
                    Optional[py_type],
                    Field(default=None, description=description),
                )
            else:
                fields[pname] = (
                    py_type,
                    Field(default=default, description=description),
                )
        else:
            fields[pname] = (py_type, Field(..., description=description))

    return create_model(name, **fields)


def _function_tool_to_langchain(tool) -> StructuredTool:
    """把一个 FastMCP FunctionTool 转成 LangChain StructuredTool。

    直接复用 tool.fn（普通 async 函数），不依赖任何 MCP session / 事件循环，
    因此在 FastAPI 主循环里调用也完全安全。
    """
    args_model = _build_args_model(tool.name, tool.parameters)
    return StructuredTool.from_function(
        coroutine=tool.fn,
        name=tool.name,
        description=tool.description,
        args_schema=args_model,
    )


async def load_mcp_tools_inprocess(force_refresh: bool = False) -> List[BaseTool]:
    """进程内加载 ERP 工具：把 FastMCP 工具直接转成 LangChain 工具。"""
    from src.mcp_server.server_main import build_mcp

    mcp = build_mcp()
    fastmcp_tools = await mcp.list_tools()
    tools = [_function_tool_to_langchain(t) for t in fastmcp_tools]
    mcp_logger.info(
        f"Loaded {len(tools)} ERP tools in-process: {[t.name for t in tools]}"
    )
    return tools


async def _load_mcp_tools_remote(force_refresh: bool = False) -> List[BaseTool]:
    """独立 MCP Server 模式：通过 Streamable HTTP（默认）或 SSE 连接。"""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    # langchain-mcp-adapters 的 transport 取值与 FastMCP 略有不同
    client_transport = "sse" if MCP_TRANSPORT == "sse" else "streamable_http"
    mcp_logger.info(
        f"Connecting to MCP Server: {MCP_URL} (transport={client_transport})"
    )
    client = MultiServerMCPClient(
        {
            "erp": {
                "url": MCP_URL,
                "transport": client_transport,
            }
        }
    )
    return await client.get_tools()


async def load_mcp_tools(force_refresh: bool = False) -> List[BaseTool]:
    """
    异步加载 MCP 工具列表（带重试和缓存）.

    Args:
        force_refresh: 强制刷新缓存

    Returns:
        MCP 工具列表，连接失败时返回空列表
    """
    global _cached_tools, _cache_time

    if not force_refresh and _cached_tools is not None:
        if time.time() - _cache_time < CACHE_TTL:
            return _cached_tools

    loader = load_mcp_tools_inprocess if MCP_USE_EMBEDDED else _load_mcp_tools_remote
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            tools = await loader(force_refresh)
            if tools:
                _cached_tools = tools
                _cache_time = time.time()
                return tools
            mcp_logger.warning("MCP Server returned 0 tools")
            return []
        except Exception as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (RETRY_BACKOFF_FACTOR ** attempt)
            mcp_logger.warning(
                f"MCP load attempt {attempt + 1}/{MAX_RETRIES} failed: {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(delay)

    mcp_logger.error(
        f"All {MAX_RETRIES} MCP load attempts failed. Last error: {last_error}. "
        f"Agent will operate without ERP tools."
    )
    return []


def load_mcp_tools_sync(force_refresh: bool = False) -> List[BaseTool]:
    """
    同步包装：加载 MCP 工具列表

    处理各种事件循环状态：
    - 无事件循环 → asyncio.run()
    - 有运行中的事件循环 → ThreadPoolExecutor + asyncio.run()
    """
    global _cached_tools, _cache_time
    if not force_refresh and _cached_tools is not None:
        if time.time() - _cache_time < CACHE_TTL:
            return _cached_tools

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, load_mcp_tools(force_refresh))
            return future.result(timeout=30)
    else:
        return asyncio.run(load_mcp_tools(force_refresh))


def invalidate_cache():
    """清除工具缓存（MCP Server 重启后调用）"""
    global _cached_tools, _cache_time
    _cached_tools = None
    _cache_time = 0
    mcp_logger.info("MCP tools cache invalidated")

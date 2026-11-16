"""
MCP 工具加载模块
通过环境变量 MCP_USE_EMBEDDED=false 可切回旧的 SSE 模式（连接独立 MCP Server）。

生产级特性：
- 连接/加载重试（指数退避）
- 结果缓存
- 优雅降级（加载失败时返回空工具列表，Agent 仍可对话）
"""
import asyncio
import json
import time
from typing import Any, List, Optional

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from ..config import MCP_URL, MCP_USE_EMBEDDED
from ..env_utils import PROJECT_ROOT, get_env
from ..log_utils import mcp_logger

# 重试配置
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # 秒
RETRY_BACKOFF_FACTOR = 2.0

# 缓存（避免重复加载）
_cached_tools: List[BaseTool] | None = None
_cache_time: float = 0
CACHE_TTL = 300  # 5分钟缓存

# 额外的 MCP Server 配置（config/mcp.json，多 server 并存）
CONFIG_MCP_PATH = PROJECT_ROOT / "config" / "mcp.json"
# config MCP 加载超时（秒）：必须小于 load_mcp_tools_sync 的硬超时
CONFIG_MCP_TIMEOUT = 60
# 熔断时间（秒）：config MCP 加载失败后 N 秒内不再尝试，避免反复撞外网
CONFIG_MCP_COOLDOWN = 300
_config_mcp_fail_until: float = 0.0
_config_mcp_lock = asyncio.Lock()

# 内置 ERP MCP（锁定，不允许禁用）
BUILTIN_MCP_NAME = "erp-builtin"
BUILTIN_MCP_DESCRIPTION = "ERP 采购管理内置工具（供应商 / 零部件 / 订单 / 库存）"

# 被禁用的 config MCP server 名集合。默认全部启用，可用环境变量
# MCP_DISABLED_SERVERS=bing-search,12306 在进程启动时就跳过，避免为不用的
# 外部服务付出建连时间（冷启动时每个远程/子进程 server 都要握手）。
_disabled_servers: set[str] = {
    name.strip()
    for name in (get_env("MCP_DISABLED_SERVERS", "") or "").split(",")
    if name.strip()
}


def _read_config_server_names() -> List[str]:
    """读取 config/mcp.json 中配置的 server 名（按文件顺序），任何异常返回 []。"""
    if not CONFIG_MCP_PATH.exists():
        return []
    try:
        with open(CONFIG_MCP_PATH, encoding="utf-8") as f:
            config = json.load(f)
        return list((config.get("mcpServers") or {}).keys())
    except Exception as e:
        mcp_logger.warning(f"Failed to read config MCP server names: {e}")
        return []


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
    import warnings

    from langchain.mcp import MCPAdapter
    from langchain_core._api import LangChainBetaWarning

    # langchain.mcp 是 beta API，导入会触发 LangChainBetaWarning，静默处理
    warnings.filterwarnings("ignore", category=LangChainBetaWarning)

    mcp_logger.info(f"Connecting to MCP Server: {MCP_URL}")
    async with MCPAdapter(MCP_URL) as adapter:
        tools = await adapter.list_tools()
    # list_tools 返回的工具持有 client 引用，上下文退出后仍可调用
    return tools


def _harden_remote_tool(tool: BaseTool, retries: int = 1) -> BaseTool:
    """给远端 MCP 工具加调用保护。

    背景：langchain.mcp 的工具每次调用都会重新连接远端（async with client），
    远端传输层错误（连接断开等）会直接传播，导致整个 agent 流崩溃。

    保护策略：
    - 调用失败重试 retries 次（远端瞬断常见）；
    - 重试耗尽后把错误转成文本返回给 LLM（让 LLM 自己重试/解释），
      而不是让异常传播崩掉整个对话。
    """
    async def _safe_call(**kwargs):
        last_err = None
        for attempt in range(retries + 1):
            try:
                return await tool.ainvoke(kwargs)
            except Exception as e:
                last_err = e
                mcp_logger.warning(
                    f"Remote MCP tool '{tool.name}' call failed "
                    f"(attempt {attempt + 1}/{retries + 1}): {str(e)[:100]}"
                )
                if attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        return (
            f"⚠ 工具 {tool.name} 调用失败（远端 MCP 服务连接异常）："
            f"{str(last_err)[:150]}。建议告知用户稍后重试。"
        )

    return StructuredTool.from_function(
        coroutine=_safe_call,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )


async def _load_config_mcp_tools_uncached() -> List[BaseTool]:
    """真正执行 config MCP 加载的内部函数（无缓存/无熔断，只做单次尝试）。"""
    if not CONFIG_MCP_PATH.exists():
        return []
    import warnings

    from langchain.mcp import MCPAdapter
    from langchain_core._api import LangChainBetaWarning

    # langchain.mcp 是 beta API，导入会触发 LangChainBetaWarning，静默处理
    warnings.filterwarnings("ignore", category=LangChainBetaWarning)

    with open(CONFIG_MCP_PATH, encoding="utf-8") as f:
        config = json.load(f)
    servers = config.get("mcpServers") or {}
    if not servers:
        mcp_logger.warning(f"{CONFIG_MCP_PATH} 中没有 mcpServers 配置")
        return []
    # 连接前就剔除已禁用的 server，避免为其建立连接（挂起的 server 会拖慢整体加载）
    active_servers = {
        n: cfg for n, cfg in servers.items() if n not in _disabled_servers
    }
    if not active_servers:
        return []
    async with MCPAdapter({"mcpServers": active_servers}) as adapter:
        tools = await adapter.list_tools()
    # 每个工具套上错误保护，防止远端断连拖垮整个对话流
    tools = [_harden_remote_tool(t) for t in tools]
    mcp_logger.info(
        f"Loaded {len(tools)} tools from config MCP servers: "
        f"{[t.name for t in tools]}"
    )
    return tools


async def load_config_mcp_tools(force_refresh: bool = False) -> List[BaseTool]:
    """从 config/mcp.json 加载额外 MCP Server 的工具（多 server 并存）。

    工具名带 server 名前缀（如 bing-cn-mcp-server_bing_search）。

    三层防拖垮保护：
    1. 超时：单次加载限时 CONFIG_MCP_TIMEOUT 秒，超时返回 []；
    2. 熔断：失败后 CONFIG_MCP_COOLDOWN 秒内不再尝试连接远端；
    3. 异常捕获：任何异常都返回 []，绝不向调用方抛错。
    """
    global _config_mcp_fail_until

    if not CONFIG_MCP_PATH.exists():
        return []
    # 熔断期内直接返回空，不重复撞外网、不重复打日志
    if not force_refresh and time.time() < _config_mcp_fail_until:
        return []

    async with _config_mcp_lock:
        # 拿到锁后再次检查熔断（防止并发场景下多个请求同时去连）
        if not force_refresh and time.time() < _config_mcp_fail_until:
            return []
        try:
            return await asyncio.wait_for(
                _load_config_mcp_tools_uncached(), timeout=CONFIG_MCP_TIMEOUT
            )
        except asyncio.TimeoutError:
            _config_mcp_fail_until = time.time() + CONFIG_MCP_COOLDOWN
            mcp_logger.error(
                f"config MCP 加载超时（>{CONFIG_MCP_TIMEOUT}s），"
                f"{CONFIG_MCP_COOLDOWN}s 内不再尝试: {CONFIG_MCP_PATH}"
            )
            return []
        except Exception as e:
            _config_mcp_fail_until = time.time() + CONFIG_MCP_COOLDOWN
            mcp_logger.error(
                f"Failed to load config MCP tools from {CONFIG_MCP_PATH}: {e}. "
                f"{CONFIG_MCP_COOLDOWN}s 内不再尝试"
            )
            return []


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
            # 注意：这里不要用 asyncio.gather 并行加载 config MCP。
            # 启动预热线程与首条请求会各自在独立事件循环里调用，
            # 并发时会复用同一个 MCP 会话上下文，实测会互相阻塞。
            tools = await loader(force_refresh)
            config_tools = await load_config_mcp_tools(force_refresh)
            all_tools = tools + config_tools
            if all_tools:
                _cached_tools = all_tools
                _cache_time = time.time()
                return all_tools
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
            try:
                return future.result(timeout=90)
            except concurrent.futures.TimeoutError:
                mcp_logger.error(
                    "MCP tools load timed out after 90s, agent will operate "
                    "without MCP tools"
                )
                return []
    else:
        return asyncio.run(load_mcp_tools(force_refresh))


def get_mcp_server_states() -> List[dict]:
    """返回所有 MCP server 的启用状态（内置 ERP + config/mcp.json）。

    每项：{name, description, enabled, locked, tool_count}
    tool_count 仅在工具已加载（缓存命中）时有值，否则为 0。
    """
    config_names = _read_config_server_names()
    config_prefixes = tuple(f"{n}_" for n in config_names)
    cached = _cached_tools or []

    builtin_count = len([t for t in cached if not t.name.startswith(config_prefixes)])
    states = [
        {
            "name": BUILTIN_MCP_NAME,
            "description": BUILTIN_MCP_DESCRIPTION,
            "enabled": True,
            "locked": True,
            "tool_count": builtin_count,
        }
    ]
    for name in config_names:
        prefix = f"{name}_"
        states.append(
            {
                "name": name,
                "description": name,
                "enabled": name not in _disabled_servers,
                "locked": False,
                "tool_count": len([t for t in cached if t.name.startswith(prefix)]),
            }
        )
    return states


def set_mcp_server_enabled(name: str, enabled: bool) -> None:
    """启用/禁用某个 config MCP server（内存态）。

    - 内置 ERP server 不允许禁用；
    - 未知 server 名抛 ValueError；
    - 切换后重置熔断并清除工具缓存，下次请求会以新工具集合重建 agent。
    """
    global _config_mcp_fail_until

    if name == BUILTIN_MCP_NAME:
        raise ValueError(f"内置 MCP server '{name}' 不允许禁用")
    known = set(_read_config_server_names())
    if name not in known:
        raise ValueError(f"未知的 MCP server: {name}")

    if enabled:
        _disabled_servers.discard(name)
    else:
        _disabled_servers.add(name)

    # 重置熔断（启用时需要能立即重连）并清工具缓存
    _config_mcp_fail_until = 0.0
    invalidate_cache()
    mcp_logger.info(f"MCP server '{name}' {'enabled' if enabled else 'disabled'}")


def invalidate_cache():
    """清除工具缓存（MCP Server 重启或 MCP 启停后调用）"""
    global _cached_tools, _cache_time
    _cached_tools = None
    _cache_time = 0
    mcp_logger.info("MCP tools cache invalidated")

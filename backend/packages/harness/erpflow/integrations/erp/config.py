"""ERP 集成层配置视图（真值在 :mod:`erpflow.config.settings`）。

MCP Server 可能作为独立进程启动，因此这里额外保证 .env 已加载；
但取值一律来自 settings，避免"同一个键两处解析"。
"""
from ...config import (
    ERP_BASE_URL,
    HTTP_MAX_CONNECTIONS,
    HTTP_TIMEOUT,
    MCP_HOST,
    MCP_PORT,
    MCP_TRANSPORT,
)
from ...config.paths import ENV_FILE, load_env

# 独立进程（uv run python -m app.mcp_server.main）自己也要读 .env
if ENV_FILE is not None:
    load_env()

__all__ = [
    "ENV_FILE",
    "ERP_BASE_URL",
    "HTTP_TIMEOUT",
    "HTTP_MAX_CONNECTIONS",
    "MCP_HOST",
    "MCP_PORT",
    "MCP_TRANSPORT",
]

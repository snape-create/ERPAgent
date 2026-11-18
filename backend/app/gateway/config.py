"""
Gateway 进程级配置

约定：进程/部署参数只读环境变量（本模块）；业务配置走 ``erpflow.config``。
"""
import os

from pydantic import BaseModel

# 默认只允许本地前端；部署时用 CORS_ORIGINS 覆盖（逗号分隔）
DEFAULT_CORS_ORIGINS = ["http://localhost:3000"]

# 后端形态：server = 网关通过 langgraph-sdk 调用 LangGraph Server（默认）
DEFAULT_AGENT_BACKEND = "server"
DEFAULT_LANGGRAPH_URL = "http://localhost:2024"

_gateway_config: "GatewayConfig | None" = None


class GatewayConfig(BaseModel):
    """Gateway 监听、跨域与 Agent 后端配置。"""

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = DEFAULT_CORS_ORIGINS
    agent_backend: str = DEFAULT_AGENT_BACKEND
    langgraph_url: str = DEFAULT_LANGGRAPH_URL


def _parse_origins(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or list(DEFAULT_CORS_ORIGINS)


def get_gateway_config() -> GatewayConfig:
    """读取并缓存 Gateway 配置（GATEWAY_HOST / GATEWAY_PORT / CORS_ORIGINS /
    AGENT_BACKEND / LANGGRAPH_URL）。"""
    global _gateway_config
    if _gateway_config is None:
        _gateway_config = GatewayConfig(
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "8000")),
            cors_origins=_parse_origins(os.getenv("CORS_ORIGINS")),
            agent_backend=os.getenv("AGENT_BACKEND", DEFAULT_AGENT_BACKEND),
            langgraph_url=os.getenv("LANGGRAPH_URL", DEFAULT_LANGGRAPH_URL),
        )
    return _gateway_config

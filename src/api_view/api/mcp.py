"""
MCP Server 管理
查询 MCP 列表 / 启用禁用某个 MCP
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agent_loader import agent_loader
from ...agent.tools.mcp_client import (
    get_mcp_server_states,
    set_mcp_server_enabled,
)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/servers")
async def list_servers():
    """获取 MCP server 列表及启用状态"""
    return {"servers": get_mcp_server_states()}


@router.post("/servers/{name}/toggle")
async def toggle_server(name: str, body: ToggleRequest):
    """启用/禁用某个 MCP server（下一条对话生效）"""
    try:
        set_mcp_server_enabled(name, body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 清除所有用户缓存的 agent session，下一条消息将按新工具集合重建 agent
    cleared = agent_loader.invalidate_sessions()
    return {
        "success": True,
        "message": f"MCP '{name}' 已{'启用' if body.enabled else '禁用'}，下一条对话生效",
        "sessions_cleared": cleared,
    }

"""
MCP Server 入口
FastMCP 实例创建，注册所有 tools，SSE 传输协议

注意：build_mcp() 同时被以下两处复用：
1. 独立运行（python -m src.mcp_server.server_main）—— 走 SSE，端口 :9000
2. FastAPI 进程内直接加载（src.agent.tools.mcp_client.load_mcp_tools_inprocess）
   —— 不再需要单独的 MCP 进程
"""
import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from fastmcp import FastMCP
from src.mcp_server.server_config import MCP_HOST, MCP_PORT, MCP_TRANSPORT
from src.mcp_server.tools.suppliers_tools import register_supplier_tools
from src.mcp_server.tools.parts_tools import register_parts_tools
from src.mcp_server.tools.order_tools import register_order_tools
from src.mcp_server.tools.inventory_tools import register_inventory_tools


def build_mcp() -> FastMCP:
    """创建并注册所有 ERP 工具的 FastMCP 实例。"""
    mcp = FastMCP(
        name="ERP-Procurement-MCP",
        instructions="摩托车零部件采购管理系统 MCP 网关，提供供应商、零部件、订单、库存查询和管理能力。",
    )

    # 注册所有工具
    register_supplier_tools(mcp)
    register_parts_tools(mcp)
    register_order_tools(mcp)
    register_inventory_tools(mcp)

    return mcp


if __name__ == "__main__":
    print(
        f"🚀 Starting MCP Server on {MCP_HOST}:{MCP_PORT} "
        f"(transport={MCP_TRANSPORT})"
    )
    build_mcp().run(transport=MCP_TRANSPORT, host=MCP_HOST, port=MCP_PORT)

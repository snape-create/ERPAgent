"""
ERP MCP 工具集构建

FastMCP 实例构建 + 全部 ERP 工具注册。被两处复用：
1. 独立进程：``app.mcp_server.main`` 启动，端口 :9000
2. 进程内直连：``erpflow.tools.mcp_client.load_mcp_tools_inprocess``
"""
from fastmcp import FastMCP

from .tools.inventory_tools import register_inventory_tools
from .tools.order_tools import register_order_tools
from .tools.parts_tools import register_parts_tools
from .tools.suppliers_tools import register_supplier_tools


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

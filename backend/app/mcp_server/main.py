"""
独立 MCP Server 进程入口（:9000）

仅负责启动传输层；工具定义在 ``erpflow.integrations.erp``（harness 侧），
因此进程内嵌模式（``MCP_USE_EMBEDDED=true``）与本进程共用同一份工具实现。
"""
from erpflow.integrations.erp.config import MCP_HOST, MCP_PORT, MCP_TRANSPORT
from erpflow.integrations.erp.server import build_mcp


def main() -> None:
    print(
        f"Starting ERP MCP Server on {MCP_HOST}:{MCP_PORT} "
        f"(transport={MCP_TRANSPORT})"
    )
    build_mcp().run(transport=MCP_TRANSPORT, host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()

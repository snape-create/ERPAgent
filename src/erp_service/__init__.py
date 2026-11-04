"""ERP 后端服务（FastAPI + PostgreSQL）

实现 Agent 侧 23 个 MCP 工具所需的 HTTP 接口，并提供摩托车零部件示例数据。
监听默认 :8081，与 Agent 的 ERP_BASE_URL 对应。

启动：
    .venv/bin/python -m src.erp_service.main
"""

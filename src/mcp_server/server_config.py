"""
MCP Server 配置模块
ERP 后端地址 + MCP 监听配置
"""
import os
from dotenv import load_dotenv

# 加载项目根目录 .env # 加载项目根目录 .env（MCP Server 独立运行，需要自己加载）
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

# Java ERP 后端地址
ERP_BASE_URL = os.getenv("ERP_BASE_URL", "http://localhost:8081")

# MCP Server 监听配置
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "9000"))
# 传输方式：streamable-http（默认，端点 /mcp）或 sse（旧版，端点 /sse）
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http").strip().lower()

# HTTP 客户端配置
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
HTTP_MAX_CONNECTIONS = int(os.getenv("HTTP_MAX_CONNECTIONS", "20"))

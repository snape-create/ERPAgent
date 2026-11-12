"""
FastAPI 应用入口
CORS 全开、路由注册、startup/shutdown 事件
"""
import sys
import os
import asyncio
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote
from contextlib import asynccontextmanager, suppress

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api.chat import router as chat_router
from .api.history import router as history_router
from .api.mcp import router as mcp_router
from .web_config import close_mongo_client
from ..agent.log_utils import web_logger
from ..agent.backends.sandbox_manager import sandbox_manager
from ..agent.config import SANDBOX_MAINTENANCE_INTERVAL_SECONDS


async def _sandbox_maintenance(stop_event: asyncio.Event) -> None:
    """后台补齐预热池、回收空闲容器。

    主请求中的 HealthMiddleware 才负责“重建 + Proxy 热替换 + 文件回填”；此处
    绝不直接调用 ``health_check_all``，否则 Agent 仍会持有旧后端引用。
    """
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(sandbox_manager.cleanup_idle_sandboxes)
            await asyncio.to_thread(sandbox_manager.ensure_warm_pool)
        except Exception as exc:
            web_logger.warning(f"Sandbox maintenance iteration failed: {exc}")


async def _warm_default_agent() -> None:
    """后台预热默认用户 Agent（含 MCP 工具加载），不阻塞 Web 启动。

    - 预热完成后 default_user 首条消息零等待（session 已构建）；
    - MCP 工具缓存是全局共享的，预热后其他用户首次请求也能命中缓存；
    - 预热失败只记 warning，首请求仍会按需构建（现有逻辑兜底）。
    """
    try:
        from .agent_loader import agent_loader

        await agent_loader.initialize("default_user")
        web_logger.info("Default user agent warmed up (MCP tools loaded)")
    except Exception as e:
        web_logger.warning(
            f"Agent warmup failed, will build on first request: {e}"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    web_logger.info("Starting ERP Agent Web Server...")
    stop_event = asyncio.Event()
    maintenance_task = None
    warmup_task = None
    try:
        # 预热失败不阻止 Web 服务启动；首个请求仍会按需创建并回退到 LocalShell。
        await asyncio.to_thread(sandbox_manager.ensure_warm_pool)
        maintenance_task = asyncio.create_task(
            _sandbox_maintenance(stop_event), name="sandbox-maintenance"
        )
        # 后台预热默认用户 Agent：提前完成 MCP 加载，首条消息不等待。
        warmup_task = asyncio.create_task(
            _warm_default_agent(), name="agent-warmup"
        )
        yield
    finally:
        web_logger.info("Shutting down ERP Agent Web Server...")
        stop_event.set()
        if maintenance_task is not None:
            maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await maintenance_task
        if warmup_task is not None:
            warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await warmup_task
        # 不销毁已认领容器：Manager 会把 user → container 映射持久化，重启后可恢复。
        await close_mongo_client()
        from ..agent.observability import shutdown
        await shutdown()


app = FastAPI(
    title="DeepAgent 智能采购助手",
    description="基于 Harness Engineering 架构的摩托车零部件采购智能助手 API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 全开（开发环境）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat_router)
app.include_router(history_router)
app.include_router(mcp_router)

# 文件下载目录（图表等生成文件）
DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "download"
DOWNLOAD_DIR.mkdir(exist_ok=True)


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """提供生成文件的 HTTP 访问。

    图片/PDF 用 inline，让浏览器直接预览；其他类型保持 attachment 下载。
    之前统一用 application/octet-stream，图片点开后只会触发下载，
    用户很容易以为“点了没反应”。
    """
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    media_type = guess_type(filename)[0] or "application/octet-stream"
    inline = (
        media_type.startswith("image/")
        or media_type == "application/pdf"
        or media_type.startswith("text/")
    )
    disposition = "inline" if inline else "attachment"
    try:
        encoded_name = quote(filename)
    except Exception:
        encoded_name = filename
    headers = {
        "Content-Disposition": f"{disposition}; filename*=utf-8''{encoded_name}",
    }
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers=headers,
    )


@app.get("/")
async def root():
    return {"message": "DeepAgent 智能采购助手 API", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

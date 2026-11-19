"""
FastAPI Gateway 装配入口

职责边界：
- 只做装配：lifespan、router 注册、静态下载路由、健康检查
- 不实现业务逻辑；业务编排在 ``app.services``，数据访问在 ``app.repositories``
- 不承载 Agent 生命周期：Agent 只活在 LangGraph Server 进程
- 运行：在 ``backend/`` 下 ``uv run python -m app.gateway.app``（监听 8000）
"""
from contextlib import asynccontextmanager
from mimetypes import guess_type
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.gateway.config import get_gateway_config
from app.gateway.routers.chat import router as chat_router
from app.gateway.routers.history import router as history_router
from app.gateway.routers.mcp import router as mcp_router
from app.gateway.routers.uploads import router as uploads_router
from app.repositories.mongo import close_mongo_client
from erpflow.config import DOWNLOAD_DIR
from erpflow.log_utils import web_logger
from erpflow.tools.path_safety import UnsafePathError, resolve_under


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    config = get_gateway_config()
    web_logger.info(f"Starting ERP Agent Gateway on {config.host}:{config.port}")
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    try:
        yield
    finally:
        web_logger.info("Shutting down ERP Agent Gateway...")
        await close_mongo_client()


def create_app() -> FastAPI:
    """创建并装配 FastAPI 应用。"""
    config = get_gateway_config()

    app = FastAPI(
        title="DeepAgent 智能采购助手",
        description="基于 Harness Engineering 架构的摩托车零部件采购智能助手 API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由注册（每个模块只暴露一个 router）
    app.include_router(chat_router)      # /api/chat/*
    app.include_router(history_router)   # /api/history
    app.include_router(mcp_router)       # /api/mcp/*
    app.include_router(uploads_router)   # /api/uploads

    @app.get("/api/download/{filename}")
    async def download_file(filename: str):
        """提供生成文件的 HTTP 访问。

        图片/PDF 用 inline，让浏览器直接预览；其他类型保持 attachment 下载。
        """
        try:
            file_path = resolve_under(DOWNLOAD_DIR, filename)
        except UnsafePathError as exc:
            raise HTTPException(status_code=400, detail=f"文件名非法: {exc}") from exc
        if not file_path.exists() or not file_path.is_file():
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
        return {"status": "healthy", "service": "erp-agent-gateway"}

    return app


# uvicorn 入口：app.gateway.app:app
app = create_app()


if __name__ == "__main__":
    import uvicorn

    _config = get_gateway_config()
    uvicorn.run(app, host=_config.host, port=_config.port)

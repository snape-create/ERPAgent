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
from erpflow.backends.artifact_store import artifact_store
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

    def _file_response(file_path, display_name: str, *, force_download: bool = False):
        """统一的文件响应：图片/PDF/文本默认 inline，其余 attachment。"""
        media_type = guess_type(display_name)[0] or "application/octet-stream"
        inline = (
            media_type.startswith("image/")
            or media_type == "application/pdf"
            or media_type.startswith("text/")
        )
        disposition = "attachment" if force_download or not inline else "inline"
        try:
            encoded_name = quote(display_name)
        except Exception:
            encoded_name = display_name
        return FileResponse(
            path=str(file_path),
            media_type=media_type,
            headers={
                "Content-Disposition": f"{disposition}; filename*=utf-8''{encoded_name}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/artifacts/{user_id}/{file_path:path}")
    async def artifact_file(user_id: str, file_path: str, download: bool = False):
        """直接提供当前用户 outputs 下的产物，不再复制到全局 download 目录。

        当前网关还没有登录会话，user_id 仍由调用方传入；生产环境应改为从
        认证态推导。路径解析与符号链接逃逸由 ArtifactStore 统一处理。
        """
        try:
            resolved = artifact_store.resolve(user_id, file_path)
        except UnsafePathError as exc:
            raise HTTPException(status_code=400, detail=f"文件路径非法: {exc}") from exc
        if not resolved.exists() or not resolved.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        return _file_response(resolved, resolved.name, force_download=download)

    @app.get("/api/download/{filename}")
    async def download_file(filename: str, download: bool = False):
        """兼容旧链接：提供 backend/download 下的生成文件。"""
        try:
            file_path = resolve_under(DOWNLOAD_DIR, filename)
        except UnsafePathError as exc:
            raise HTTPException(status_code=400, detail=f"文件名非法: {exc}") from exc
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
        return _file_response(file_path, filename, force_download=download)

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

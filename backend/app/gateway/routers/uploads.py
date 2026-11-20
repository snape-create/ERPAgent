"""用户上传路由：写入宿主机 user-data/uploads。

当前网关没有独立鉴权层，``user_id`` 与聊天接口一样由请求携带；生产接入登录后
必须改为从服务端 session 推导，不能信任客户端传入的 user_id。

上传文件平铺在 ``{data_root}/users/{user_id}/user-data/uploads``，沙箱启动时
通过 bind mount 看到 ``/mnt/user-data/uploads``。
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from erpflow.backends.user_data import ensure_user_data_dirs, uploads_dir
from erpflow.config import SANDBOX_MAX_UPLOAD_MB, SANDBOX_MAX_USER_UPLOAD_MB
from erpflow.log_utils import web_logger
from erpflow.tools.path_safety import UnsafePathError, resolve_under, safe_filename

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

MAX_UPLOAD_BYTES = SANDBOX_MAX_UPLOAD_MB * 1024 * 1024
MAX_USER_UPLOAD_BYTES = SANDBOX_MAX_USER_UPLOAD_MB * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024

# 配额检查和最终 rename 必须在同一用户锁内完成，否则两个并发上传会同时
# 读到旧 usage，双双通过上限检查。
#
# asyncio.Lock 会绑定到首次使用的 event loop；TestClient 可能为每组请求创建
# 不同的 loop，复用同一个 Lock 会卡死。因此 key 带上 loop identity。
_user_locks: dict[tuple[int, str], asyncio.Lock] = {}
_user_locks_guard = threading.Lock()


def _lock_for(user_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), user_id)
    with _user_locks_guard:
        return _user_locks.setdefault(key, asyncio.Lock())


def _unique_target(base: Path, filename: str) -> Path:
    """避免同名覆盖；文件名已通过 safe_filename 校验。"""
    name = safe_filename(filename)
    # .part 是服务端临时文件后缀；用户不能把它当最终文件名，否则
    # _current_usage/list_uploads 都会忽略它，从而绕过配额和列表。
    if name.lower().endswith(".part"):
        raise UnsafePathError("文件名不能以 .part 结尾")
    target = base / name
    if not target.exists():
        return target
    stem = target.stem or target.name
    suffix = target.suffix
    return base / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


def _file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "filename": path.name,
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
    }


def _current_usage(base: Path) -> int:
    """当前用户 uploads 目录已占用字节数（忽略 .part）。"""
    return sum(
        p.stat().st_size
        for p in base.iterdir()
        if p.is_file() and not p.name.endswith(".part")
    )


async def _save_upload(upload: UploadFile, target: Path, usage_start: int = 0) -> int:
    """流式写入并限制单文件/用户总大小；先写 .part 再原子替换。"""
    size = 0
    tmp = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        with tmp.open("wb") as fh:
            while chunk := await upload.read(_CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过上限 {SANDBOX_MAX_UPLOAD_MB} MB: {target.name}",
                    )
                if (
                    MAX_USER_UPLOAD_BYTES > 0
                    and usage_start + size > MAX_USER_UPLOAD_BYTES
                ):
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"用户上传空间超过上限 {SANDBOX_MAX_USER_UPLOAD_MB} MB: "
                            f"{target.name}"
                        ),
                    )
                fh.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail=f"文件为空: {target.name}")
        tmp.replace(target)
        return size
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@router.post("")
async def upload_files(
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """上传一个或多个文件到当前用户的 uploads 目录。"""
    user_id = (user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id 不能为空")

    lock = _lock_for(user_id)
    async with lock:
        ensure_user_data_dirs(user_id)
        base = uploads_dir(user_id)
        base.mkdir(parents=True, exist_ok=True)

        saved = []
        usage = _current_usage(base)
        for upload in files:
            try:
                target = _unique_target(base, upload.filename or "")
                size = await _save_upload(upload, target, usage_start=usage)
            except UnsafePathError as exc:
                raise HTTPException(status_code=400, detail=f"文件名非法: {exc}") from exc
            usage += size
            saved.append({"filename": target.name, "size": size})

    web_logger.info(f"Uploaded {len(saved)} file(s) for user {user_id}")
    return {"user_id": user_id, "files": saved}


@router.get("")
async def list_uploads(user_id: str):
    """列出当前用户的上传文件。"""
    ensure_user_data_dirs(user_id)
    base = uploads_dir(user_id)
    base.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (
            _file_info(p)
            for p in base.iterdir()
            if p.is_file() and not p.name.endswith(".part")
        ),
        key=lambda item: item["filename"],
    )
    return {"user_id": user_id, "files": files}


@router.delete("/{filename}")
async def delete_upload(filename: str, user_id: str):
    """删除一个上传文件（只允许 uploads 目录内的单文件名）。"""
    ensure_user_data_dirs(user_id)
    base = uploads_dir(user_id)
    try:
        target = resolve_under(base, filename)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=f"文件名非法: {exc}") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")
    target.unlink()
    web_logger.info(f"Deleted upload {filename} for user {user_id}")
    return {"user_id": user_id, "deleted": filename}
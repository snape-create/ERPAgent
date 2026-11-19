"""user-data 宿主机目录的 TTL 清理。

只清理 uploads/outputs；workspace 本来就在容器 tmpfs，不需要清理。

安全约束：
- 只删除超过 TTL 的**文件**，再移除空目录；
- 读取 ``user-data/.meta.json`` 得到 user_id；元数据缺失的目录跳过；
- 如果该用户仍有运行中的 ``erp-sandbox-<user>-*`` 容器，整户跳过；
- Docker 不可用时直接跳过（宁可不清，也不误删活跃用户数据）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..config import (
    SANDBOX_DATA_ROOT,
    SANDBOX_OUTPUTS_TTL_DAYS,
    SANDBOX_UPLOADS_TTL_DAYS,
)
from ..log_utils import sandbox_logger
from .user_data import META_FILENAME

_CONTAINER_PREFIX = "erp-sandbox-"


def _container_prefix_for_user(user_id: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in user_id)
    return f"{_CONTAINER_PREFIX}{safe}-"


def _active_container_names() -> list[str] | None:
    """返回运行中的 erp-sandbox 容器名；Docker 不可用返回 None。"""
    try:
        import docker

        client = docker.from_env(timeout=5)
    except Exception as exc:
        sandbox_logger.warning(f"Skip user-data cleanup, Docker unavailable: {exc}")
        return None
    try:
        return [
            container.name
            for container in client.containers.list(filters={"name": _CONTAINER_PREFIX})
        ]
    except Exception as exc:
        sandbox_logger.warning(f"Skip user-data cleanup, cannot list containers: {exc}")
        return None
    finally:
        client.close()


def _delete_old_files(root, cutoff_ts: float) -> int:
    """删除 root 下 mtime 早于 cutoff 的文件，并移除空目录。"""
    if not root.is_dir():
        return 0
    removed = 0
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink() or path.is_file():
            try:
                if path.stat().st_mtime < cutoff_ts:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                sandbox_logger.warning(f"Failed to remove expired file {path}: {exc}")
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                # 非空目录，后续轮次再试。
                pass
    return removed


def cleanup_expired_user_data(
    *,
    now: datetime | None = None,
    uploads_ttl_days: int | None = None,
    outputs_ttl_days: int | None = None,
    active_container_names: list[str] | None = None,
) -> int:
    """按 TTL 清理 uploads/outputs，返回删除的文件数。"""
    uploads_days = SANDBOX_UPLOADS_TTL_DAYS if uploads_ttl_days is None else uploads_ttl_days
    outputs_days = SANDBOX_OUTPUTS_TTL_DAYS if outputs_ttl_days is None else outputs_ttl_days
    if uploads_days <= 0 and outputs_days <= 0:
        return 0

    if active_container_names is None:
        active_container_names = _active_container_names()
    if active_container_names is None:
        return 0

    users_root = SANDBOX_DATA_ROOT / "users"
    if not users_root.is_dir():
        return 0

    now = now or datetime.now()
    uploads_cutoff = (now - timedelta(days=max(0, uploads_days))).timestamp()
    outputs_cutoff = (now - timedelta(days=max(0, outputs_days))).timestamp()

    removed = 0
    for user_dir in users_root.iterdir():
        if not user_dir.is_dir():
            continue
        data_root = user_dir / "user-data"
        meta_path = data_root / META_FILENAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            user_id = str(meta["user_id"])
        except (OSError, ValueError, KeyError, TypeError):
            sandbox_logger.debug(f"Skip cleanup for {data_root}: missing/invalid metadata")
            continue

        prefix = _container_prefix_for_user(user_id)
        if any(name.startswith(prefix) for name in active_container_names):
            sandbox_logger.debug(f"Skip cleanup for active user {user_id}")
            continue

        if uploads_days > 0:
            removed += _delete_old_files(data_root / "uploads", uploads_cutoff)
        if outputs_days > 0:
            removed += _delete_old_files(data_root / "outputs", outputs_cutoff)

    if removed:
        sandbox_logger.info(f"Removed {removed} expired user-data file(s)")
    return removed


__all__ = ["cleanup_expired_user_data"]
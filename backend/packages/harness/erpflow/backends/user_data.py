"""用户级 user-data 宿主机路径与挂载。

Q1=B / Q2=A / Q3=A：
- 每个用户一个容器；
- 只持久化 uploads 与 outputs；
- workspace 留在容器 tmpfs，不进宿主机。

这里不依赖 Docker，网关上传接口、清理任务和沙箱创建都能复用。
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from ..config import (
    SANDBOX_DATA_ROOT,
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_UPLOADS_DIR,
)

META_FILENAME = ".meta.json"


def user_data_dir_name(user_id: str) -> str:
    """把 user_id 稳定映射成安全的单段目录名。

    只做字符替换会有碰撞（``a/b`` 与 ``a_b``），因此附加 user_id 的短哈希，
    保证不同用户不会共享同一个宿主机目录。
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in user_id)
    if not safe or safe in {".", ".."}:
        safe = "user"
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


def user_data_root(user_id: str) -> Path:
    """``{data_root}/users/{safe_id}/user-data``。"""
    return SANDBOX_DATA_ROOT / "users" / user_data_dir_name(user_id) / "user-data"


def uploads_dir(user_id: str) -> Path:
    return user_data_root(user_id) / "uploads"


def outputs_dir(user_id: str) -> Path:
    return user_data_root(user_id) / "outputs"


def ensure_user_data_dirs(user_id: str) -> Path:
    """创建 uploads/outputs 并写入用户元数据，返回 user-data 根目录。"""
    root = user_data_root(user_id)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    meta = root / META_FILENAME
    if not meta.exists():
        meta.write_text(
            json.dumps(
                {
                    "user_id": user_id,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return root


def user_data_mounts(user_id: str) -> dict[str, str]:
    """返回 ``host_path -> container_path`` 的 bind mount 映射。"""
    root = ensure_user_data_dirs(user_id)
    return {
        str(root / "uploads"): SANDBOX_UPLOADS_DIR,
        str(root / "outputs"): SANDBOX_OUTPUTS_DIR,
    }


__all__ = [
    "META_FILENAME",
    "ensure_user_data_dirs",
    "outputs_dir",
    "uploads_dir",
    "user_data_dir_name",
    "user_data_mounts",
    "user_data_root",
]
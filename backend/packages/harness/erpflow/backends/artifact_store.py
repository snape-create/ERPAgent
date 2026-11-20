"""用户产物的统一访问层。

产物已经在沙箱启动时通过 bind mount 落到宿主机：

``backend/.sandbox/users/<user>/user-data/outputs``
``<-> /mnt/user-data/outputs``

因此浏览器不需要再从容器读一遍，也不需要复制到 ``backend/download``。
本模块只负责把“用户 + 输出目录相对路径”解析成一个安全的宿主机路径，
并生成稳定的同源 URL。未来接 S3/MinIO 时，保持同样的接口即可。
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ..config import SANDBOX_OUTPUTS_DIR
from ..tools.path_safety import (
    UnsafePathError,
    resolve_relative_under,
    safe_relative_path,
)
from .user_data import outputs_dir


def output_relative_path(remote_path: str) -> str | None:
    """把沙箱内路径转成 outputs 下的相对路径。

    接受：

    - ``/mnt/user-data/outputs/report.html``
    - ``/mnt/user-data/outputs/charts/a.png``
    - ``report.html`` / ``charts/a.png``（相对 outputs）

    不接受 outputs 之外的路径，也不接受 ``..``、反斜杠等不安全输入。
    """
    if not isinstance(remote_path, str) or not remote_path or "\x00" in remote_path:
        return None
    if "\\" in remote_path:
        return None

    path = PurePosixPath(remote_path)
    outputs = PurePosixPath(SANDBOX_OUTPUTS_DIR)

    if path.is_absolute():
        try:
            relative = path.relative_to(outputs)
        except ValueError:
            return None
    else:
        relative = path

    if not relative.parts:
        return None
    try:
        return safe_relative_path(relative.as_posix())
    except UnsafePathError:
        return None


class ArtifactStore:
    """本地文件系统实现；接口语义保持不变即可替换为对象存储。"""

    def root_for(self, user_id: str) -> Path:
        return outputs_dir(user_id)

    def resolve(self, user_id: str, relative_path: str) -> Path:
        """解析到宿主机 outputs 内，越界/符号链接逃逸抛 UnsafePathError。"""
        return resolve_relative_under(self.root_for(user_id), relative_path)

    def url_for(self, user_id: str, relative_path: str) -> str:
        safe_path = safe_relative_path(relative_path)
        return (
            f"/api/artifacts/{quote(user_id, safe='')}/"
            f"{quote(safe_path, safe='/')}"
        )


artifact_store = ArtifactStore()

__all__ = ["ArtifactStore", "artifact_store", "output_relative_path"]

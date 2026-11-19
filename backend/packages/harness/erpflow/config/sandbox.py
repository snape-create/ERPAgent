"""沙箱配置：``config/sandbox_config.toml`` + 环境变量兜底。

优先级：**TOML > 环境变量 > 代码默认值**。

历史原因这里曾把常量散在 ``config.py`` 里并被 5 个模块各自复制路径；现在：
- 文件位置由 :mod:`erpflow.config.paths` 决定（唯一来源）
- 读取/优先级只在本模块实现
- 上层模块 import 本模块导出的常量或 :class:`SandboxSettings`
"""
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

from .paths import BACKEND_ROOT, CONFIG_DIR

SANDBOX_TOML = CONFIG_DIR / "sandbox_config.toml"


def _load_sandbox_toml() -> dict:
    """读取沙箱 TOML；文件缺失或格式错误时回退为空 dict，不影响业务。"""
    try:
        with open(SANDBOX_TOML, "rb") as f:
            return tomllib.load(f).get("sandbox", {})
    except (OSError, tomllib.TOMLDecodeError):
        print(f"⚠️ sandbox_config.toml 读取失败，回退到环境变量/默认值: {SANDBOX_TOML}")
        return {}


SANDBOX_TOML_DATA = _load_sandbox_toml()


def _to_int(value):
    return int(value)


def _to_bool(value):
    """把环境变量字符串/TOML 布尔统一成 bool。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _sandbox_get(section: str, key: str, env_key: str | None, default, cast=None):
    """优先级：TOML > 环境变量 > 默认值。"""
    data = SANDBOX_TOML_DATA if not section else SANDBOX_TOML_DATA.get(section, {})
    value = data.get(key)
    if value is None and env_key:
        value = os.getenv(env_key)
    if value is None:
        return default
    return cast(value) if cast else value


SANDBOX_IMAGE = _sandbox_get("", "image", "SANDBOX_IMAGE", "python:latest")
SANDBOX_USER_DATA_ROOT = _sandbox_get(
    "", "user_data_root", "SANDBOX_USER_DATA_ROOT", "/mnt/user-data"
)
SANDBOX_WORK_DIR = _sandbox_get(
    "", "work_dir", "SANDBOX_WORK_DIR", f"{SANDBOX_USER_DATA_ROOT}/workspace"
)
SANDBOX_OUTPUTS_DIR = f"{SANDBOX_USER_DATA_ROOT}/outputs"
SANDBOX_UPLOADS_DIR = f"{SANDBOX_USER_DATA_ROOT}/uploads"
SANDBOX_SKILLS_DIR = _sandbox_get("", "skills_dir", "SANDBOX_SKILLS_DIR", "/skills")
SANDBOX_MEMORIES_DIR = _sandbox_get("", "memories_dir", "SANDBOX_MEMORIES_DIR", "/memories")
SANDBOX_PYTHON_PACKAGES_DIR = _sandbox_get(
    "", "python_packages_dir", "SANDBOX_PYTHON_PACKAGES_DIR", "/opt/pydeps"
)

# 宿主机 user-data 根目录：``backend/.sandbox/``（可被 TOML/环境变量覆盖）。
_raw_data_root = _sandbox_get("", "data_root", "SANDBOX_DATA_ROOT", ".sandbox")
SANDBOX_DATA_ROOT = Path(_raw_data_root).expanduser()
if not SANDBOX_DATA_ROOT.is_absolute():
    SANDBOX_DATA_ROOT = (BACKEND_ROOT / SANDBOX_DATA_ROOT).resolve()
else:
    SANDBOX_DATA_ROOT = SANDBOX_DATA_ROOT.resolve()

# Q1=B / Q3=A：uploads + outputs 持久化到宿主机，workspace 保持 tmpfs。
# 启用后预热池无法在认领时改变 bind mount，因此 warm pool 会自动停用。
SANDBOX_USER_DATA_MOUNTS = _sandbox_get(
    "", "user_data_mounts", "SANDBOX_USER_DATA_MOUNTS", True, _to_bool
)
SANDBOX_MAX_UPLOAD_MB = _sandbox_get(
    "", "max_upload_mb", "SANDBOX_MAX_UPLOAD_MB", 25, _to_int
)
SANDBOX_MAX_USER_UPLOAD_MB = _sandbox_get(
    "", "max_user_upload_mb", "SANDBOX_MAX_USER_UPLOAD_MB", 200, _to_int
)
SANDBOX_ALLOW_PRIVATE_WEB = _sandbox_get(
    "", "allow_private_web", "SANDBOX_ALLOW_PRIVATE_WEB", False, _to_bool
)
SANDBOX_MAX_FETCH_MB = _sandbox_get(
    "", "max_fetch_mb", "SANDBOX_MAX_FETCH_MB", 5, _to_int
)
SANDBOX_MAX_ARCHIVE_MB = _sandbox_get(
    "", "max_archive_mb", "SANDBOX_MAX_ARCHIVE_MB", 20, _to_int
)
SANDBOX_MAX_EXTRACTED_MB = _sandbox_get(
    "", "max_extracted_mb", "SANDBOX_MAX_EXTRACTED_MB", 50, _to_int
)
SANDBOX_MAX_ARCHIVE_FILES = _sandbox_get(
    "", "max_archive_files", "SANDBOX_MAX_ARCHIVE_FILES", 500, _to_int
)
SANDBOX_UPLOADS_TTL_DAYS = _sandbox_get(
    "", "uploads_ttl_days", "SANDBOX_UPLOADS_TTL_DAYS", 30, _to_int
)
SANDBOX_OUTPUTS_TTL_DAYS = _sandbox_get(
    "", "outputs_ttl_days", "SANDBOX_OUTPUTS_TTL_DAYS", 30, _to_int
)
SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS = _sandbox_get(
    "", "user_data_cleanup_interval_seconds",
    "SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS", 3600, _to_int,
)
SANDBOX_WARM_POOL_SIZE = _sandbox_get("", "warm_pool_size", "SANDBOX_WARM_POOL_SIZE", 1, _to_int)
SANDBOX_IDLE_TIMEOUT_MINUTES = _sandbox_get(
    "", "idle_timeout_minutes", "SANDBOX_IDLE_TIMEOUT_MINUTES", 30, _to_int
)
SANDBOX_MAINTENANCE_INTERVAL_SECONDS = _sandbox_get(
    "", "maintenance_interval_seconds", "SANDBOX_MAINTENANCE_INTERVAL_SECONDS", 60, _to_int
)
SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS = _sandbox_get(
    "", "health_check_interval_seconds", "SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS", 30, _to_int
)
# 仅本地开发：沙箱不可用时允许从宿主机 DOWNLOAD_DIR 读取文件。
# 生产必须保持 false，避免把服务器文件系统暴露给模型。
SANDBOX_ALLOW_LOCAL_FALLBACK = _sandbox_get(
    "", "allow_local_fallback", "SANDBOX_ALLOW_LOCAL_FALLBACK", False, _to_bool
)

# SandboxConfig dataclass 的默认值（backends/sandbox_setup.py 引用）
SANDBOX_CONTAINER = {
    "read_only": _sandbox_get("container", "read_only", None, True),
    "memory_limit": _sandbox_get("container", "memory_limit", None, "512m"),
    "cpu_limit": _sandbox_get("container", "cpu_limit", None, 1.0),
    "network_mode": _sandbox_get("container", "network_mode", None, "none"),
    "tmpfs_size": _sandbox_get("container", "tmpfs_size", None, "128m"),
    "workspace_size": _sandbox_get("container", "workspace_size", None, "256m"),
    "skills_size": _sandbox_get("container", "skills_size", None, "128m"),
    "pydeps_size": _sandbox_get("container", "pydeps_size", None, "512m"),
    "uploads_size": _sandbox_get("container", "uploads_size", None, "128m"),
    "outputs_size": _sandbox_get("container", "outputs_size", None, "256m"),
    "drop_all_caps": _sandbox_get("container", "drop_all_caps", None, True),
    "use_seccomp": _sandbox_get("container", "use_seccomp", None, True),
    "seccomp_path": _sandbox_get("container", "seccomp_path", None, ""),
    "runtimes": _sandbox_get("container", "runtimes", None, ["python"]),
    # 安全容器创建失败时是否允许退回无防护容器；默认 fail closed。
    "allow_insecure_fallback": _sandbox_get(
        "container", "allow_insecure_fallback", None, False, _to_bool
    ),
}


class SandboxSettings(BaseModel):
    """沙箱配置的类型化视图（供 settings 聚合与文档生成）。"""

    image: str = SANDBOX_IMAGE
    user_data_root: str = SANDBOX_USER_DATA_ROOT
    work_dir: str = SANDBOX_WORK_DIR
    outputs_dir: str = SANDBOX_OUTPUTS_DIR
    uploads_dir: str = SANDBOX_UPLOADS_DIR
    skills_dir: str = SANDBOX_SKILLS_DIR
    memories_dir: str = SANDBOX_MEMORIES_DIR
    data_root: str = str(SANDBOX_DATA_ROOT)
    user_data_mounts: bool = SANDBOX_USER_DATA_MOUNTS
    max_upload_mb: int = SANDBOX_MAX_UPLOAD_MB
    max_user_upload_mb: int = SANDBOX_MAX_USER_UPLOAD_MB
    allow_private_web: bool = SANDBOX_ALLOW_PRIVATE_WEB
    max_fetch_mb: int = SANDBOX_MAX_FETCH_MB
    max_archive_mb: int = SANDBOX_MAX_ARCHIVE_MB
    max_extracted_mb: int = SANDBOX_MAX_EXTRACTED_MB
    max_archive_files: int = SANDBOX_MAX_ARCHIVE_FILES
    uploads_ttl_days: int = SANDBOX_UPLOADS_TTL_DAYS
    outputs_ttl_days: int = SANDBOX_OUTPUTS_TTL_DAYS
    user_data_cleanup_interval_seconds: int = SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS
    python_packages_dir: str = SANDBOX_PYTHON_PACKAGES_DIR
    warm_pool_size: int = SANDBOX_WARM_POOL_SIZE
    idle_timeout_minutes: int = SANDBOX_IDLE_TIMEOUT_MINUTES
    maintenance_interval_seconds: int = SANDBOX_MAINTENANCE_INTERVAL_SECONDS
    health_check_interval_seconds: int = SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS
    allow_local_fallback: bool = SANDBOX_ALLOW_LOCAL_FALLBACK
    container: dict = SANDBOX_CONTAINER

"""沙箱配置：``config/sandbox_config.toml`` + 环境变量兜底。

优先级：**TOML > 环境变量 > 代码默认值**。

历史原因这里曾把常量散在 ``config.py`` 里并被 5 个模块各自复制路径；现在：
- 文件位置由 :mod:`erpflow.config.paths` 决定（唯一来源）
- 读取/优先级只在本模块实现
- 上层模块 import 本模块导出的常量或 :class:`SandboxSettings`
"""
import os
import tomllib

from pydantic import BaseModel

from .paths import CONFIG_DIR

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
SANDBOX_WORK_DIR = _sandbox_get("", "work_dir", "SANDBOX_WORK_DIR", "/workspace")
SANDBOX_SKILLS_DIR = _sandbox_get("", "skills_dir", "SANDBOX_SKILLS_DIR", "/skills")
SANDBOX_MEMORIES_DIR = _sandbox_get("", "memories_dir", "SANDBOX_MEMORIES_DIR", "/memories")
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

# SandboxConfig dataclass 的默认值（backends/sandbox_setup.py 引用）
SANDBOX_CONTAINER = {
    "read_only": _sandbox_get("container", "read_only", None, True),
    "memory_limit": _sandbox_get("container", "memory_limit", None, "512m"),
    "cpu_limit": _sandbox_get("container", "cpu_limit", None, 1.0),
    "network_mode": _sandbox_get("container", "network_mode", None, "bridge"),
    "tmpfs_size": _sandbox_get("container", "tmpfs_size", None, "128m"),
    "drop_all_caps": _sandbox_get("container", "drop_all_caps", None, True),
    "use_seccomp": _sandbox_get("container", "use_seccomp", None, True),
    "seccomp_path": _sandbox_get("container", "seccomp_path", None, ""),
    "runtimes": _sandbox_get("container", "runtimes", None, ["python"]),
}


class SandboxSettings(BaseModel):
    """沙箱配置的类型化视图（供 settings 聚合与文档生成）。"""

    image: str = SANDBOX_IMAGE
    work_dir: str = SANDBOX_WORK_DIR
    skills_dir: str = SANDBOX_SKILLS_DIR
    memories_dir: str = SANDBOX_MEMORIES_DIR
    warm_pool_size: int = SANDBOX_WARM_POOL_SIZE
    idle_timeout_minutes: int = SANDBOX_IDLE_TIMEOUT_MINUTES
    maintenance_interval_seconds: int = SANDBOX_MAINTENANCE_INTERVAL_SECONDS
    health_check_interval_seconds: int = SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS
    container: dict = SANDBOX_CONTAINER

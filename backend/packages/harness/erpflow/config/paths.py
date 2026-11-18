"""路径常量与环境变量加载 —— 全项目唯一来源。

目录约定（相对 ``backend/``）：

    BACKEND_ROOT  backend/            uv 项目根，pyproject.toml 所在
    PROJECT_ROOT  <repo>/             仓库根（frontend/ 与 backend/ 的父目录）
    SKILLS_DIR    backend/skills/     技能数据资产（main/、procurement/）
    DOWNLOAD_DIR  backend/download/   生成的图表/文档，由 /api/download 提供
    CONFIG_DIR    backend/config/     sandbox_config.toml、mcp.json

更上层的模块（工具、网关）一律从这里 import，不要再写 ``parents[n]`` 或
``Path(__file__).parent / ".."``——这是历史上"下载目录三份副本"的根因。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# backend/packages/harness/erpflow/config/paths.py -> parents[4] == backend/
BACKEND_ROOT = Path(__file__).resolve().parents[4]
PROJECT_ROOT = BACKEND_ROOT.parent
SKILLS_DIR = BACKEND_ROOT / "skills"
DOWNLOAD_DIR = BACKEND_ROOT / "download"
CONFIG_DIR = BACKEND_ROOT / "config"


def find_env_file() -> Path | None:
    """定位 .env：ERP_ENV_FILE > backend/.env > 仓库根 .env。"""
    override = os.getenv("ERP_ENV_FILE")
    candidates: list[Path] = [Path(override)] if override else []
    candidates += [BACKEND_ROOT / ".env", PROJECT_ROOT / ".env"]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_env() -> Path | None:
    """加载 .env；返回实际使用的文件（没有就返回 None）。

    注意 ``override=False``：shell / CI / pytest 显式设置的变量优先于 .env。
    这样测试可以安全地关掉追踪、改端口，而不用去改用户的 .env。
    """
    env_file = find_env_file()
    if env_file is None:
        print("⚠️ .env not found (checked ERP_ENV_FILE / backend/.env / repo root .env)")
        return None
    load_dotenv(env_file, override=False)
    return env_file


def get_env(key: str, default: str | None = None) -> str | None:
    """取环境变量。"""
    return os.getenv(key, default)


def get_env_int(key: str, default: int = 0) -> int:
    """取整数环境变量。"""
    return int(os.getenv(key, str(default)))


def get_env_bool(key: str, default: bool = False) -> bool:
    """取布尔环境变量（1/true/yes/on 为真）。"""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# 模块导入即加载 .env（保持原有行为：任何入口都会拿到 .env 配置）
ENV_FILE = load_env()

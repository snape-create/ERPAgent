"""
全局配置模块
LLM、Store、Checkpointer、沙箱连接参数
"""
import os
import tomllib
from langchain_deepseek import ChatDeepSeek
from .env_utils import get_env, get_env_int, PROJECT_ROOT

# ============ LLM 配置 ============
LLM_MODEL = get_env("LLM_MODEL", "deepseek-flash")
LLM_BASE_URL = get_env("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = get_env("DEEPSEEK_API_KEY", "")
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 4096
LLM_REASONING_EFFORT = get_env("LLM_REASONING_EFFORT", "high")


# ============ jev决策模型配置 ============
ENDPOINT = "https://ph8.co/openai/v1"

def get_llm(*, thinking: bool = True, timeout: float | None = None,
            max_retries: int = 2, max_tokens: int = LLM_MAX_TOKENS) -> ChatDeepSeek:
    """获取 DeepSeek 模型实例。

    主 Agent 使用思考模式；需要强制结构化输出的内部评审器使用
    非思考模式，避免 DeepSeek 拒绝 ``tool_choice`` 参数。
    """
    common_kwargs = {
        "model": LLM_MODEL,
        "base_url": LLM_BASE_URL,
        "api_key": LLM_API_KEY,
        "max_tokens": max_tokens,
        "max_retries": max_retries,
    }
    if timeout is not None:
        common_kwargs["timeout"] = timeout
    if thinking:
        return ChatDeepSeek(
            **common_kwargs,
            reasoning_effort=LLM_REASONING_EFFORT,
            extra_body={"thinking": {"type": "enabled"}},
        )
    model = ChatDeepSeek(
        **common_kwargs,
        temperature=LLM_TEMPERATURE,
        extra_body={"thinking": {"type": "disabled"}},
    )

    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        profile["structured_output"] = False

    return model


# ============ MongoDB 配置 ============
MONGODB_URI = get_env("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = get_env("MONGODB_DB_NAME", "erp_agent")

# ============ MCP Server 配置 ============
MCP_SERVER_URL = get_env("MCP_SERVER_URL", "http://localhost:9000")
# 是否把 MCP 工具内嵌到 FastAPI 进程直接加载（默认 true，无需单独启动 MCP Server）
MCP_USE_EMBEDDED = get_env("MCP_USE_EMBEDDED", "true").strip().lower() in ("1", "true", "yes", "on")
# 独立 MCP Server 的传输方式：streamable-http（默认）或 sse（旧版）
MCP_TRANSPORT = get_env("MCP_TRANSPORT", "streamable-http").strip().lower()
# 传输端点：streamable-http 默认 /mcp，sse 默认 /sse
MCP_PATH = get_env(
    "MCP_PATH", "/sse" if MCP_TRANSPORT == "sse" else "/mcp"
)
MCP_URL = f"{MCP_SERVER_URL}{MCP_PATH}"
# 兼容旧名称
MCP_SSE_URL = MCP_URL

# ============ 沙箱配置（TOML 优先，环境变量兜底）============
# 配置文件统一放在项目根目录 config/ 下
_SANDBOX_TOML = PROJECT_ROOT / "config" / "sandbox_config.toml"


def _load_sandbox_toml() -> dict:
    """读取沙箱 TOML；文件缺失或格式错误时回退为空 dict，不影响业务。"""
    try:
        with open(_SANDBOX_TOML, "rb") as f:
            return tomllib.load(f).get("sandbox", {})
    except (OSError, tomllib.TOMLDecodeError):
        print(f"⚠️ sandbox_config.toml 读取失败，回退到环境变量/默认值: {_SANDBOX_TOML}")
        return {}


_SANDBOX_TOML_DATA = _load_sandbox_toml()


def _sandbox_get(section: str, key: str, env_key: str | None, default, cast=None):
    """优先级：TOML > 环境变量 > 默认值。"""
    data = _SANDBOX_TOML_DATA if not section else _SANDBOX_TOML_DATA.get(section, {})
    value = data.get(key)
    if value is None and env_key:
        env = os.getenv(env_key)
        value = env if env is not None else None
    if value is None:
        return default
    return cast(value) if cast else value


def _to_int(value):
    return int(value)


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

# ============ Store 命名空间 ============
SKILLS_STORE_NAMESPACE = ("persisted-skills",)
PREFERENCES_STORE_NAMESPACE = ("user-preferences",)


def skills_store_namespace(user_id: str) -> tuple[str, str]:
    """Return the isolated Store namespace for one user's custom Skills."""
    return (*SKILLS_STORE_NAMESPACE, user_id)

# ============ Agent 配置 ============
MAX_MODEL_CALLS = get_env_int("MAX_MODEL_CALLS", 50)
MAX_TOOL_CALLS = get_env_int("MAX_TOOL_CALLS", 30)
SUMMARIZATION_THRESHOLD = 0.85  # 85% 上下文窗口时触发摘要

# ============ 中断配置 ============
INTERRUPT_ON_TOOLS = {
    "order_create": {"allowed_decisions": ["approve", "reject"]},
    "order_update": {"allowed_decisions": ["approve", "reject"]},
}

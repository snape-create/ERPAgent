"""配置单一入口。

模块结构：

    paths.py     路径常量（BACKEND_ROOT / SKILLS_DIR / DOWNLOAD_DIR …）+ .env 加载
    sandbox.py   sandbox_config.toml + 环境变量（TOML > env > 默认）
    settings.py  LLM / JEV / Mongo / MCP / Agent / 追踪（env → pydantic → 单例）
    llm.py       get_llm()

对上层保持历史常量名（``from erpflow.config import SANDBOX_IMAGE`` 继续可用），
它们是在导入时从 settings 求值一次的普通常量。
"""
from .llm import get_llm
from .paths import (
    BACKEND_ROOT,
    CONFIG_DIR,
    DOWNLOAD_DIR,
    ENV_FILE,
    PROJECT_ROOT,
    SKILLS_DIR,
    find_env_file,
    get_env,
    get_env_bool,
    get_env_int,
    load_env,
)
from .sandbox import (
    SANDBOX_ALLOW_LOCAL_FALLBACK,
    SANDBOX_ALLOW_PRIVATE_WEB,
    SANDBOX_CONTAINER,
    SANDBOX_DATA_ROOT,
    SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS,
    SANDBOX_IDLE_TIMEOUT_MINUTES,
    SANDBOX_IMAGE,
    SANDBOX_MAINTENANCE_INTERVAL_SECONDS,
    SANDBOX_MAX_ARCHIVE_FILES,
    SANDBOX_MAX_ARCHIVE_MB,
    SANDBOX_MAX_EXTRACTED_MB,
    SANDBOX_MAX_FETCH_MB,
    SANDBOX_MAX_UPLOAD_MB,
    SANDBOX_MAX_USER_UPLOAD_MB,
    SANDBOX_MEMORIES_DIR,
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_OUTPUTS_TTL_DAYS,
    SANDBOX_PYTHON_PACKAGES_DIR,
    SANDBOX_SKILLS_DIR,
    SANDBOX_TOML,
    SANDBOX_TOML_DATA,
    SANDBOX_UPLOADS_DIR,
    SANDBOX_UPLOADS_TTL_DAYS,
    SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS,
    SANDBOX_USER_DATA_MOUNTS,
    SANDBOX_USER_DATA_ROOT,
    SANDBOX_WARM_POOL_SIZE,
    SANDBOX_WORK_DIR,
    SandboxSettings,
    _load_sandbox_toml,
    _sandbox_get,
    _to_bool,
    _to_int,
)
from .settings import (
    AgentSettings,
    ErpSettings,
    JevSettings,
    LLMSettings,
    McpSettings,
    MongoSettings,
    Settings,
    TracingSettings,
    get_settings,
    reset_settings,
)

# ---------------------------------------------------------------- 常量视图
# 导入期求值一次，保持与历史行为一致（改环境需重启/重导入才生效）
_settings = get_settings()

LLM_MODEL = _settings.llm.model
LLM_BASE_URL = _settings.llm.base_url
LLM_API_KEY = _settings.llm.api_key
LLM_TEMPERATURE = _settings.llm.temperature
LLM_MAX_TOKENS = _settings.llm.max_tokens
LLM_REASONING_EFFORT = _settings.llm.reasoning_effort

DEFAULT_JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_ENDPOINT = _settings.jev.endpoint
# 兼容旧名字（历史脚本/实验代码仍 import ENDPOINT）
ENDPOINT = JEV_ENDPOINT
JEV_MODEL = _settings.jev.model
REVIEW_ROUTER_PROVIDER = _settings.jev.provider

MONGODB_URI = _settings.mongo.uri
MONGODB_DB_NAME = _settings.mongo.db_name
MONGODB_SELECTION_TIMEOUT_MS = _settings.mongo.selection_timeout_ms

ERP_BASE_URL = _settings.erp.base_url
HTTP_TIMEOUT = _settings.erp.http_timeout_seconds
HTTP_MAX_CONNECTIONS = _settings.erp.http_max_connections

MCP_HOST = _settings.mcp.host
MCP_PORT = _settings.mcp.port

MCP_SERVER_URL = _settings.mcp.server_url
MCP_USE_EMBEDDED = _settings.mcp.use_embedded
MCP_TRANSPORT = _settings.mcp.transport
MCP_PATH = _settings.mcp.path
MCP_URL = _settings.mcp.url
# 兼容旧名称
MCP_SSE_URL = MCP_URL

MAX_MODEL_CALLS = _settings.agent.max_model_calls
MAX_TOOL_CALLS = _settings.agent.max_tool_calls
SUMMARIZATION_THRESHOLD = _settings.agent.summarization_threshold
INTERRUPT_ON_TOOLS = _settings.agent.interrupt_on_tools

TRACING_ENABLED = _settings.tracing.enabled
TRACING_PROJECT = _settings.tracing.project

# ------------------------------------------------------------ Store 命名空间
SKILLS_STORE_NAMESPACE = ("persisted-skills",)
PREFERENCES_STORE_NAMESPACE = ("user-preferences",)


def skills_store_namespace(user_id: str) -> tuple[str, str]:
    """Return the isolated Store namespace for one user's custom Skills."""
    return (*SKILLS_STORE_NAMESPACE, user_id)


__all__ = [
    # paths
    "BACKEND_ROOT", "PROJECT_ROOT", "SKILLS_DIR", "DOWNLOAD_DIR", "CONFIG_DIR",
    "ENV_FILE", "find_env_file", "load_env", "get_env", "get_env_int", "get_env_bool",
    # sandbox
    "SANDBOX_IMAGE", "SANDBOX_WORK_DIR", "SANDBOX_SKILLS_DIR", "SANDBOX_MEMORIES_DIR",
    "SANDBOX_USER_DATA_ROOT", "SANDBOX_OUTPUTS_DIR", "SANDBOX_UPLOADS_DIR",
    "SANDBOX_DATA_ROOT", "SANDBOX_USER_DATA_MOUNTS", "SANDBOX_PYTHON_PACKAGES_DIR",
    "SANDBOX_MAX_UPLOAD_MB", "SANDBOX_MAX_USER_UPLOAD_MB",
    "SANDBOX_ALLOW_PRIVATE_WEB", "SANDBOX_MAX_FETCH_MB", "SANDBOX_MAX_ARCHIVE_MB",
    "SANDBOX_MAX_EXTRACTED_MB", "SANDBOX_MAX_ARCHIVE_FILES",
    "SANDBOX_UPLOADS_TTL_DAYS", "SANDBOX_OUTPUTS_TTL_DAYS",
    "SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS",
    "SANDBOX_WARM_POOL_SIZE", "SANDBOX_IDLE_TIMEOUT_MINUTES",
    "SANDBOX_MAINTENANCE_INTERVAL_SECONDS", "SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS",
    "SANDBOX_ALLOW_LOCAL_FALLBACK",
    "SANDBOX_CONTAINER", "SANDBOX_TOML", "SANDBOX_TOML_DATA", "SandboxSettings",
    "_load_sandbox_toml", "_sandbox_get", "_to_int", "_to_bool",
    # settings
    "Settings", "LLMSettings", "JevSettings", "ErpSettings", "MongoSettings", "McpSettings",
    "AgentSettings", "TracingSettings", "get_settings", "reset_settings",
    # llm
    "get_llm", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", "LLM_TEMPERATURE",
    "LLM_MAX_TOKENS", "LLM_REASONING_EFFORT",
    # endpoints / integrations
    "DEFAULT_JEV_ENDPOINT", "JEV_ENDPOINT", "JEV_MODEL", "ENDPOINT", "REVIEW_ROUTER_PROVIDER",
    "MONGODB_URI", "MONGODB_DB_NAME", "MONGODB_SELECTION_TIMEOUT_MS",
    "ERP_BASE_URL", "HTTP_TIMEOUT", "HTTP_MAX_CONNECTIONS", "MCP_HOST", "MCP_PORT",
    "MCP_SERVER_URL", "MCP_USE_EMBEDDED", "MCP_TRANSPORT", "MCP_PATH", "MCP_URL", "MCP_SSE_URL",
    # agent
    "MAX_MODEL_CALLS", "MAX_TOOL_CALLS", "SUMMARIZATION_THRESHOLD", "INTERRUPT_ON_TOOLS",
    "TRACING_ENABLED", "TRACING_PROJECT",
    "SKILLS_STORE_NAMESPACE", "PREFERENCES_STORE_NAMESPACE", "skills_store_namespace",
]

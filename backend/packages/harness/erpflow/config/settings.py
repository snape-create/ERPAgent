"""业务配置：环境变量 → pydantic 模型 → 单例。

分工：
- 进程/部署参数（host/port/cors）在 ``app.gateway.config``，只读 env
- 业务参数（LLM/Mongo/MCP/沙箱/Agent/追踪）在本模块
- 沙箱还有一层 TOML 覆盖，见 :mod:`erpflow.config.sandbox`

``get_settings()`` 返回单例；测试要改环境时用 ``reset_settings()`` 复位。
"""
from pydantic import BaseModel, Field

from .paths import get_env, get_env_bool, get_env_int
from .sandbox import SandboxSettings


class LLMSettings(BaseModel):
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096
    reasoning_effort: str = "high"


class JevSettings(BaseModel):
    """审查路由（决策模型）配置。"""

    provider: str = "deepseek"
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    api_key: str = ""
    model: str = "jev-1.13.0"
    timeout_seconds: float = 8.0


class MongoSettings(BaseModel):
    uri: str = "mongodb://localhost:27017"
    db_name: str = "erp_agent"
    # 远程库首次连接要走 DNS+TCP+认证，放宽避免误判不可用
    selection_timeout_ms: int = 10000


class ErpSettings(BaseModel):
    """外部 ERP（真实系统或 Mock）的接入参数。"""

    base_url: str = "http://localhost:8081"
    http_timeout_seconds: int = 30
    http_max_connections: int = 20


class McpSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 9000
    server_url: str = "http://localhost:9000"
    use_embedded: bool = True
    transport: str = "streamable-http"
    path: str = "/mcp"

    @property
    def url(self) -> str:
        return f"{self.server_url}{self.path}"


class AgentSettings(BaseModel):
    max_model_calls: int = 50
    max_tool_calls: int = 30
    # 上下文用到 85% 窗口时触发摘要
    summarization_threshold: float = 0.85
    interrupt_on_tools: dict = Field(default_factory=dict)


class TracingSettings(BaseModel):
    """LangSmith 追踪开关。

    默认关闭：本地开发/CI 不应因为一个 key 而反复连外网（曾经每次超时 10s）。
    """

    enabled: bool = False
    project: str = "erp-agent"


def _interrupt_policy() -> dict:
    """HITL 中断策略：只有订单写操作允许"改参数再提交"。"""
    return {
        "order_create": {"allowed_decisions": ["approve", "reject", "edit"]},
        "order_update": {"allowed_decisions": ["approve", "reject", "edit"]},
        # 状态变更只带 id 和目标状态，改参数没有意义
        "order_update_status": {"allowed_decisions": ["approve", "reject"]},
    }


def _mcp_transport() -> str:
    return (get_env("MCP_TRANSPORT", "streamable-http") or "").strip().lower()


def _mcp_path() -> str:
    default = "/sse" if _mcp_transport() == "sse" else "/mcp"
    return get_env("MCP_PATH", default)


class Settings(BaseModel):
    """全部业务配置的聚合视图。"""

    llm: LLMSettings
    jev: JevSettings
    mongo: MongoSettings
    erp: ErpSettings
    mcp: McpSettings
    sandbox: SandboxSettings
    agent: AgentSettings
    tracing: TracingSettings


def _build_settings() -> Settings:
    transport = _mcp_transport()
    return Settings(
        llm=LLMSettings(
            model=get_env("LLM_MODEL", "deepseek-flash"),
            base_url=get_env("LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=get_env("DEEPSEEK_API_KEY", ""),
            temperature=0.1,
            max_tokens=4096,
            reasoning_effort=get_env("LLM_REASONING_EFFORT", "high"),
        ),
        jev=JevSettings(
            provider=get_env("REVIEW_ROUTER_PROVIDER", "deepseek"),
            endpoint=get_env("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone"),
            api_key=get_env("TYPESAFE_API_KEY", ""),
            model=get_env("TYPESAFE_MODEL", "jev-1.13.0"),
            timeout_seconds=float(get_env("TYPESAFE_TIMEOUT", "8")),
        ),
        mongo=MongoSettings(
            uri=get_env("MONGODB_URI", "mongodb://localhost:27017"),
            db_name=get_env("MONGODB_DB_NAME", "erp_agent"),
            selection_timeout_ms=get_env_int("MONGODB_SELECTION_TIMEOUT_MS", 10000),
        ),
        erp=ErpSettings(
            base_url=get_env("ERP_BASE_URL", "http://localhost:8081"),
            http_timeout_seconds=get_env_int("HTTP_TIMEOUT", 30),
            http_max_connections=get_env_int("HTTP_MAX_CONNECTIONS", 20),
        ),
        mcp=McpSettings(
            host=get_env("MCP_HOST", "0.0.0.0"),
            port=get_env_int("MCP_PORT", 9000),
            server_url=get_env("MCP_SERVER_URL", "http://localhost:9000"),
            use_embedded=get_env_bool("MCP_USE_EMBEDDED", True),
            transport=transport,
            path=_mcp_path(),
        ),
        sandbox=SandboxSettings(),
        agent=AgentSettings(
            max_model_calls=get_env_int("MAX_MODEL_CALLS", 50),
            max_tool_calls=get_env_int("MAX_TOOL_CALLS", 30),
            interrupt_on_tools=_interrupt_policy(),
        ),
        tracing=TracingSettings(
            enabled=get_env_bool("LANGSMITH_TRACING", False),
            project=get_env("LANGSMITH_PROJECT", "erp-agent"),
        ),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """返回进程级配置单例（首次调用时构建）。"""
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings


def reset_settings() -> None:
    """测试用：丢弃缓存，下次调用重新读环境。"""
    global _settings
    _settings = None

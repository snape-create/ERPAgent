"""配置行为锁定：路径、沙箱优先级、settings 单例、端点可配置。

对应 ``erpflow/config/`` 包。断言的是**行为**（谁覆盖谁、默认值是多少、路径指向
哪里），不绑定内部实现细节——重命名模块不应让这些测试失效。
"""
import os
import tomllib
from pathlib import Path

import pytest

from erpflow import config as erp_config
from erpflow.config import sandbox as sandbox_config
from erpflow.config import settings as settings_module
from erpflow.config.paths import BACKEND_ROOT, DOWNLOAD_DIR, PROJECT_ROOT, SKILLS_DIR, find_env_file

BACKEND_DIR = Path(__file__).resolve().parents[1]
SANDBOX_TOML = BACKEND_DIR / "config" / "sandbox_config.toml"


def _toml_sandbox() -> dict:
    with open(SANDBOX_TOML, "rb") as f:
        return tomllib.load(f)["sandbox"]


# ---------------------------------------------------------------- 路径常量


def test_backend_root_points_at_backend_dir():
    assert BACKEND_ROOT == BACKEND_DIR
    assert (BACKEND_ROOT / "pyproject.toml").is_file()


def test_project_root_is_repo_root():
    assert PROJECT_ROOT == BACKEND_DIR.parent
    assert (PROJECT_ROOT / "README.md").is_file()


def test_skills_and_download_dirs():
    assert SKILLS_DIR == BACKEND_DIR / "skills"
    assert {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()} >= {"main", "procurement"}
    assert DOWNLOAD_DIR == BACKEND_DIR / "download"


def test_find_env_file_prefers_existing_file():
    env_file = find_env_file()
    assert env_file is None or env_file.is_file()
    if (PROJECT_ROOT / ".env").is_file() and not (BACKEND_DIR / ".env").is_file():
        assert env_file == PROJECT_ROOT / ".env"


# ---------------------------------------------------------------- 沙箱 TOML


def test_sandbox_toml_lives_in_backend_config():
    assert sandbox_config.SANDBOX_TOML == SANDBOX_TOML
    assert SANDBOX_TOML.is_file()


def test_sandbox_toml_data_matches_file():
    assert sandbox_config.SANDBOX_TOML_DATA == _toml_sandbox()


def test_missing_toml_falls_back_to_empty(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sandbox_config, "SANDBOX_TOML", tmp_path / "missing.toml")
    assert sandbox_config._load_sandbox_toml() == {}
    assert "读取失败" in capsys.readouterr().out


SANDBOX_SCALARS = [
    ("SANDBOX_IMAGE", "image"),
    ("SANDBOX_WORK_DIR", "work_dir"),
    ("SANDBOX_SKILLS_DIR", "skills_dir"),
    ("SANDBOX_MEMORIES_DIR", "memories_dir"),
    ("SANDBOX_WARM_POOL_SIZE", "warm_pool_size"),
    ("SANDBOX_IDLE_TIMEOUT_MINUTES", "idle_timeout_minutes"),
    ("SANDBOX_MAINTENANCE_INTERVAL_SECONDS", "maintenance_interval_seconds"),
    ("SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS", "health_check_interval_seconds"),
]


@pytest.mark.parametrize("constant,toml_key", SANDBOX_SCALARS)
def test_sandbox_scalar_matches_toml(constant, toml_key):
    assert getattr(erp_config, constant) == _toml_sandbox()[toml_key]


@pytest.mark.parametrize(
    "container_key",
    [
        "read_only",
        "memory_limit",
        "cpu_limit",
        "network_mode",
        "tmpfs_size",
        "drop_all_caps",
        "use_seccomp",
        "seccomp_path",
        "runtimes",
    ],
)
def test_sandbox_container_default_matches_toml(container_key):
    expected = _toml_sandbox()["container"][container_key]
    assert erp_config.SANDBOX_CONTAINER[container_key] == expected


# --------------------------------------------------------- 三级优先级


def test_toml_wins_over_env(monkeypatch):
    monkeypatch.setattr(sandbox_config, "SANDBOX_TOML_DATA", {"warm_pool_size": 7})
    monkeypatch.setenv("SANDBOX_WARM_POOL_SIZE", "9")
    assert sandbox_config._sandbox_get(
        "", "warm_pool_size", "SANDBOX_WARM_POOL_SIZE", 1, sandbox_config._to_int
    ) == 7


def test_env_wins_over_default(monkeypatch):
    monkeypatch.setattr(sandbox_config, "SANDBOX_TOML_DATA", {})
    monkeypatch.setenv("SANDBOX_WARM_POOL_SIZE", "9")
    assert sandbox_config._sandbox_get(
        "", "warm_pool_size", "SANDBOX_WARM_POOL_SIZE", 1, sandbox_config._to_int
    ) == 9


def test_default_used_when_toml_and_env_absent(monkeypatch):
    monkeypatch.setattr(sandbox_config, "SANDBOX_TOML_DATA", {})
    monkeypatch.delenv("SANDBOX_WARM_POOL_SIZE", raising=False)
    assert sandbox_config._sandbox_get(
        "", "warm_pool_size", "SANDBOX_WARM_POOL_SIZE", 1, sandbox_config._to_int
    ) == 1


def test_sectioned_lookup_only_reads_its_section(monkeypatch):
    monkeypatch.setattr(
        sandbox_config, "SANDBOX_TOML_DATA", {"container": {"memory_limit": "256m"}}
    )
    assert sandbox_config._sandbox_get("container", "memory_limit", None, "512m") == "256m"
    assert sandbox_config._sandbox_get("", "memory_limit", None, "512m") == "512m"


# ------------------------------------------------------------- Settings


def test_settings_singleton_is_cached():
    assert settings_module.get_settings() is settings_module.get_settings()


def test_reset_settings_rebuilds(monkeypatch):
    before = settings_module.get_settings()
    settings_module.reset_settings()
    monkeypatch.setenv("LLM_MODEL", "deepseek-test-model")
    rebuilt = settings_module.get_settings()
    try:
        assert rebuilt is not before
        assert rebuilt.llm.model == "deepseek-test-model"
    finally:
        settings_module.reset_settings()


def test_settings_covers_all_sections():
    s = settings_module.get_settings()
    assert s.llm.model and s.llm.base_url
    assert s.mongo.uri.startswith("mongodb://")
    assert s.mcp.transport in ("streamable-http", "sse")
    assert s.sandbox.warm_pool_size >= 0
    assert s.agent.max_model_calls > 0
    assert isinstance(s.tracing.enabled, bool)


def test_mcp_url_is_server_url_plus_path():
    s = settings_module.get_settings()
    assert s.mcp.url == f"{s.mcp.server_url}{s.mcp.path}"
    assert erp_config.MCP_URL == s.mcp.url
    assert erp_config.MCP_SSE_URL == erp_config.MCP_URL


def test_mcp_path_follows_transport():
    expected = "/sse" if erp_config.MCP_TRANSPORT == "sse" else "/mcp"
    assert erp_config.MCP_PATH == expected


def test_interrupt_policy_only_allows_edit_on_order_write():
    policy = erp_config.INTERRUPT_ON_TOOLS
    assert policy["order_create"]["allowed_decisions"] == ["approve", "reject", "edit"]
    assert policy["order_update_status"]["allowed_decisions"] == ["approve", "reject"]


def test_skills_store_namespace_is_user_scoped():
    assert erp_config.skills_store_namespace("alice") == ("persisted-skills", "alice")


def test_env_overrides_dotenv(monkeypatch):
    """显式环境变量必须优先于 .env（否则 CI/测试无法关掉追踪或改端口）。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    settings_module.reset_settings()
    try:
        assert settings_module.get_settings().tracing.enabled is False
    finally:
        settings_module.reset_settings()


# -------------------------------------------------------- 网关进程配置


def test_gateway_config_reads_env(monkeypatch):
    from app.gateway import config as gateway_config

    monkeypatch.setattr(gateway_config, "_gateway_config", None)
    monkeypatch.setenv("GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("GATEWAY_PORT", "9099")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    cfg = gateway_config.get_gateway_config()
    assert (cfg.host, cfg.port) == ("127.0.0.1", 9099)
    assert cfg.cors_origins == ["http://a.test", "http://b.test"]


def test_gateway_config_is_cached(monkeypatch):
    from app.gateway import config as gateway_config

    monkeypatch.setattr(gateway_config, "_gateway_config", None)
    assert gateway_config.get_gateway_config() is gateway_config.get_gateway_config()


# ------------------------------------------------- 路径常量的单一来源


def test_download_dir_is_single_source():
    """下载目录只允许有一个定义；各消费方必须是同一个对象。"""
    from app.gateway import app as gateway_app
    from erpflow.tools import document_generator, download_sandbox_file

    assert download_sandbox_file.DOWNLOAD_DIR is DOWNLOAD_DIR
    assert document_generator.LOCAL_DOWNLOAD_DIR is DOWNLOAD_DIR
    assert gateway_app.DOWNLOAD_DIR is DOWNLOAD_DIR


def test_skills_dir_is_single_source():
    from erpflow.agent import build as agent_build

    # install_skill 不再写宿主全局 skills，只有 bootstrap/skills sync 使用 SKILLS_DIR。
    assert agent_build.SKILLS_DIR is SKILLS_DIR


# ------------------------------------------------- P1-2 修好的两个缺陷


def test_integrations_erp_env_file_resolves_to_real_file():
    """独立 MCP 进程也要能读到 .env（此前路径指到 erpflow/.env，不存在）。"""
    from erpflow.integrations.erp import config as erp_client_config

    assert erp_client_config.ENV_FILE is None or erp_client_config.ENV_FILE.is_file()
    if (PROJECT_ROOT / ".env").is_file() and not (BACKEND_DIR / ".env").is_file():
        assert erp_client_config.ENV_FILE == PROJECT_ROOT / ".env"


def test_decision_endpoint_is_configurable():
    """端点来自 TYPESAFE_URL；默认值是官方 systemone，可被实例参数覆盖。"""
    from erpflow.review import JevRouter

    assert "typesafe" in erp_config.DEFAULT_JEV_ENDPOINT
    assert erp_config.JEV_ENDPOINT == os.getenv(
        "TYPESAFE_URL", erp_config.DEFAULT_JEV_ENDPOINT
    )
    assert erp_config.ENDPOINT == erp_config.JEV_ENDPOINT  # 兼容别名
    assert JevRouter(api_key="k").endpoint == erp_config.JEV_ENDPOINT
    assert JevRouter(api_key="k", endpoint="http://local.test/v1").endpoint == "http://local.test/v1"

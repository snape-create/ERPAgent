"""Q1=B / Q2=A / Q3=A：user-data 挂载与 warm pool 停用。"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docker
import pytest

from erpflow.backends import sandbox_entry, sandbox_manager, user_data
from erpflow.backends.sandbox_entry import create_sandbox_container
from erpflow.backends.sandbox_setup import SandboxConfig, create_secure_sandbox
from erpflow.backends.user_data import user_data_dir_name, user_data_mounts


def test_user_data_dir_name_is_stable_and_collision_resistant():
    assert user_data_dir_name("alice") == user_data_dir_name("alice")
    # 字符替换会碰撞的 ID 必须得到不同目录。
    assert user_data_dir_name("a/b") != user_data_dir_name("a_b")
    # 不能产生路径穿越。
    assert "/" not in user_data_dir_name("../x")
    assert user_data_dir_name("..") not in {".", ".."}


def test_user_data_mounts_creates_uploads_and_outputs(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    monkeypatch.setattr(sandbox_entry, "SANDBOX_USER_DATA_MOUNTS", True)

    mounts = user_data_mounts("alice")

    assert mounts == {
        str(tmp_path / "users" / user_data_dir_name("alice") / "user-data" / "uploads"):
            "/mnt/user-data/uploads",
        str(tmp_path / "users" / user_data_dir_name("alice") / "user-data" / "outputs"):
            "/mnt/user-data/outputs",
    }
    for host_path in mounts:
        assert (tmp_path / "users" / user_data_dir_name("alice") / "user-data").joinpath(
            host_path.rsplit("/", 1)[-1]
        ).is_dir()


def test_create_sandbox_container_passes_user_mounts(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    monkeypatch.setattr(sandbox_entry, "SANDBOX_USER_DATA_MOUNTS", True)
    captured = {}

    class FakeSandbox:
        def execute(self, command):
            captured["command"] = command
            return SimpleNamespace(exit_code=0, output="")

    def fake_create(config):
        captured["config"] = config
        return FakeSandbox()

    monkeypatch.setattr(sandbox_entry, "create_secure_sandbox", fake_create)

    create_sandbox_container("erp-sandbox-alice", user_id="alice")

    config = captured["config"]
    assert "/mnt/user-data/uploads" in config.volumes.values()
    assert "/mnt/user-data/outputs" in config.volumes.values()
    assert "/workspace" in captured["command"]
    assert "/mnt/user-data/workspace" in captured["command"]


def test_warm_sandbox_has_no_user_mounts(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    monkeypatch.setattr(sandbox_entry, "SANDBOX_USER_DATA_MOUNTS", True)
    captured = {}

    class FakeSandbox:
        def execute(self, command):
            return SimpleNamespace(exit_code=0, output="")

    def fake_create(config):
        captured["config"] = config
        return FakeSandbox()

    monkeypatch.setattr(sandbox_entry, "create_secure_sandbox", fake_create)
    create_sandbox_container("erp-sandbox-warm-1", user_id="")

    assert captured["config"].volumes == {}


def test_ensure_warm_pool_is_disabled_when_user_data_mounts_enabled(monkeypatch):
    manager = object.__new__(sandbox_manager.SandboxManager)
    manager._lock = threading.Lock()
    manager._entries = {}
    manager._warm_creating = 0
    manager._warm_pool_size = 2
    monkeypatch.setattr(sandbox_manager, "SANDBOX_USER_DATA_MOUNTS", True)
    monkeypatch.setattr(
        sandbox_manager,
        "create_sandbox_container",
        lambda *args, **kwargs: pytest.fail("warm pool must not create containers"),
    )

    manager.ensure_warm_pool()

    assert manager._entries == {}
    assert manager._warm_creating == 0


def test_secure_sandbox_passes_volumes_to_docker(monkeypatch, tmp_path):
    fake_client = MagicMock()
    fake_client.containers.get.side_effect = docker.errors.NotFound("missing")
    fake_client.containers.run.return_value = MagicMock(id="abc123")
    host = tmp_path / "uploads"
    host.mkdir()
    config = SandboxConfig(
        name="erp-sandbox-test",
        volumes={str(host): "/mnt/user-data/uploads"},
    )

    with patch("erpflow.backends.sandbox_setup.docker.from_env", return_value=fake_client), \
         patch("erpflow.backends.sandbox_setup.CustomOpenSandbox"), \
         patch("erpflow.backends.sandbox_setup._init_runtimes"):
        create_secure_sandbox(config)

    kwargs = fake_client.containers.run.call_args.kwargs
    assert kwargs["volumes"] == {
        str(host): {"bind": "/mnt/user-data/uploads", "mode": "rw"}
    }
    assert kwargs["network_mode"] == "none"
    # P0-6：依赖目录必须 exec，且 PIP_TARGET/PYTHONPATH 指向它。
    assert "exec" in kwargs["tmpfs"]["/opt/pydeps"]
    assert kwargs["environment"]["PIP_TARGET"] == "/opt/pydeps"
    assert kwargs["environment"]["PYTHONPATH"] == "/opt/pydeps"
    # bind mount 已覆盖 uploads，不应再为同一路径声明 tmpfs。
    assert "/mnt/user-data/uploads" not in kwargs["tmpfs"]
    # 未 bind 的 outputs 仍必须有可写 tmpfs，避免只读 rootfs 上 mkdir 失败。
    assert "/mnt/user-data/outputs" in kwargs["tmpfs"]

def test_tmpfs_sizes_come_from_config():
    fake_client = MagicMock()
    fake_client.containers.get.side_effect = docker.errors.NotFound("missing")
    fake_client.containers.run.return_value = MagicMock(id="abc123")
    config = SandboxConfig(
        name="erp-sandbox-test-sizes",
        workspace_size="111m",
        skills_size="222m",
        pydeps_size="333m",
    )

    with patch("erpflow.backends.sandbox_setup.docker.from_env", return_value=fake_client), \
         patch("erpflow.backends.sandbox_setup.CustomOpenSandbox"), \
         patch("erpflow.backends.sandbox_setup._init_runtimes"):
        create_secure_sandbox(config)

    tmpfs = fake_client.containers.run.call_args.kwargs["tmpfs"]
    assert tmpfs["/workspace"].endswith("size=111m")
    assert tmpfs[config.work_dir].endswith("size=111m")
    assert tmpfs["/skills"].endswith("size=222m")
    assert tmpfs["/opt/pydeps"].endswith("size=333m")


def test_missing_seccomp_profile_fails_closed(tmp_path):
    config = SandboxConfig(
        name="erp-sandbox-no-seccomp",
        use_seccomp=True,
        seccomp_path=str(tmp_path / "missing.json"),
    )
    with pytest.raises(RuntimeError, match="seccomp"):
        create_secure_sandbox(config)


def test_invalid_seccomp_profile_fails_closed(tmp_path):
    profile = tmp_path / "seccomp.json"
    profile.write_text("{not-json", encoding="utf-8")
    config = SandboxConfig(
        name="erp-sandbox-bad-seccomp",
        use_seccomp=True,
        seccomp_path=str(profile),
    )
    with pytest.raises(RuntimeError, match="seccomp"):
        create_secure_sandbox(config)

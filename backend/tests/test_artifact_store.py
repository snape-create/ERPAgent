"""产物访问层：用户隔离、outputs 相对路径、路径穿越与下载 URL。"""
from __future__ import annotations

import pytest

from erpflow.backends import user_data
from erpflow.backends.artifact_store import (
    ArtifactStore,
    artifact_store,
    output_relative_path,
)
from erpflow.tools.path_safety import UnsafePathError


@pytest.mark.parametrize(
    ("remote_path", "expected"),
    [
        ("/mnt/user-data/outputs/report.html", "report.html"),
        ("/mnt/user-data/outputs/charts/a.png", "charts/a.png"),
        ("report.html", "report.html"),
        ("charts/a.png", "charts/a.png"),
    ],
)
def test_output_relative_path_accepts_outputs(remote_path, expected):
    assert output_relative_path(remote_path) == expected


@pytest.mark.parametrize(
    "remote_path",
    [
        "/workspace/report.html",
        "/mnt/user-data/outputs/../secret.txt",
        "/mnt/user-data/outputs/",
        "a\\b.html",
        "",
        "/mnt/user-data/outputs/\x00x",
    ],
)
def test_output_relative_path_rejects_unsafe(remote_path):
    assert output_relative_path(remote_path) is None


def test_artifact_store_resolves_user_outputs(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    root = user_data.outputs_dir("alice")
    (root / "charts").mkdir(parents=True)
    target = root / "charts" / "a.png"
    target.write_bytes(b"png")

    store = ArtifactStore()
    assert store.resolve("alice", "charts/a.png") == target.resolve()
    assert store.url_for("alice", "charts/a.png") == "/api/artifacts/alice/charts/a.png"


def test_artifact_store_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    with pytest.raises(UnsafePathError):
        artifact_store.resolve("alice", "../secret.txt")


def test_artifact_store_rejects_symlink_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    root = user_data.outputs_dir("alice")
    root.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")

    with pytest.raises(UnsafePathError):
        artifact_store.resolve("alice", "link.txt")


def test_artifact_url_encodes_chinese_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    url = artifact_store.url_for("alice", "采购报告.html")
    assert url == "/api/artifacts/alice/%E9%87%87%E8%B4%AD%E6%8A%A5%E5%91%8A.html"


def test_sandbox_cache_index_retries_after_failure():
    from erpflow.backends.sandbox_cache import SandboxCache

    class Collection:
        def __init__(self):
            self.calls = 0

        def create_index(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("mongo down")

    cache = SandboxCache()
    collection = Collection()
    cache._ensure_unique_index(collection)
    assert cache._index_ready is False
    assert collection.calls == 1

    cache._ensure_unique_index(collection)
    assert cache._index_ready is True
    assert collection.calls == 2

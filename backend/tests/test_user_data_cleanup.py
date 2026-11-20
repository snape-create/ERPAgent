"""user-data TTL 清理：只删过期文件、跳过活跃用户、Docker 不可用时不清。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from erpflow.backends import user_data, user_data_cleanup


def _make_user(tmp_path, monkeypatch, user_id="alice"):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    monkeypatch.setattr(user_data_cleanup, "SANDBOX_DATA_ROOT", tmp_path)
    user_data.ensure_user_data_dirs(user_id)
    return user_data.uploads_dir(user_id), user_data.outputs_dir(user_id)


def _age(path, days=60):
    old = (datetime.now() - timedelta(days=days)).timestamp()
    os.utime(path, (old, old))


def test_removes_expired_files_and_keeps_recent(tmp_path, monkeypatch):
    uploads, outputs = _make_user(tmp_path, monkeypatch)
    old_upload = uploads / "old.txt"
    old_upload.write_text("old", encoding="utf-8")
    _age(old_upload)

    old_output = outputs / "old.md"
    old_output.write_text("old", encoding="utf-8")
    _age(old_output)

    recent = uploads / "recent.txt"
    recent.write_text("recent", encoding="utf-8")

    removed = user_data_cleanup.cleanup_expired_user_data(
        active_container_names=[],
        uploads_ttl_days=30,
        outputs_ttl_days=30,
    )

    assert removed == 2
    assert not old_upload.exists()
    assert not old_output.exists()
    assert recent.exists()


def test_skips_active_user(tmp_path, monkeypatch):
    uploads, _ = _make_user(tmp_path, monkeypatch)
    old = uploads / "old.txt"
    old.write_text("old", encoding="utf-8")
    _age(old)

    removed = user_data_cleanup.cleanup_expired_user_data(
        active_container_names=["erp-sandbox-alice-abc123"],
        uploads_ttl_days=30,
        outputs_ttl_days=30,
    )

    assert removed == 0
    assert old.exists()


def test_skips_when_docker_unavailable(tmp_path, monkeypatch):
    uploads, outputs = _make_user(tmp_path, monkeypatch)
    old = uploads / "old.txt"
    old.write_text("old", encoding="utf-8")
    _age(old)
    monkeypatch.setattr(user_data_cleanup, "_active_container_names", lambda: None)

    assert user_data_cleanup.cleanup_expired_user_data() == 0
    assert old.exists()


def test_skips_directory_without_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(user_data_cleanup, "SANDBOX_DATA_ROOT", tmp_path)
    data = tmp_path / "users" / "broken-user" / "user-data"
    uploads = data / "uploads"
    uploads.mkdir(parents=True)
    old = uploads / "old.txt"
    old.write_text("old", encoding="utf-8")
    _age(old)

    removed = user_data_cleanup.cleanup_expired_user_data(
        active_container_names=[],
        uploads_ttl_days=30,
        outputs_ttl_days=30,
    )

    assert removed == 0
    assert old.exists()


def test_removes_empty_subdirectories(tmp_path, monkeypatch):
    uploads, _ = _make_user(tmp_path, monkeypatch)
    nested = uploads / "sub"
    nested.mkdir()
    old = nested / "old.txt"
    old.write_text("old", encoding="utf-8")
    _age(old)

    removed = user_data_cleanup.cleanup_expired_user_data(
        active_container_names=[],
        uploads_ttl_days=30,
        outputs_ttl_days=30,
    )

    assert removed == 1
    assert not nested.exists()

def test_skips_active_user_by_lease(tmp_path, monkeypatch):
    uploads, _ = _make_user(tmp_path, monkeypatch)
    old = uploads / "old.txt"
    old.write_text("old", encoding="utf-8")
    _age(old)

    removed = user_data_cleanup.cleanup_expired_user_data(
        active_user_ids={"alice"},
        uploads_ttl_days=30,
        outputs_ttl_days=30,
    )

    assert removed == 0
    assert old.exists()


def test_stale_running_container_does_not_block_ttl_without_lease(tmp_path, monkeypatch):
    uploads, _ = _make_user(tmp_path, monkeypatch)
    old = uploads / "old.txt"
    old.write_text("old", encoding="utf-8")
    _age(old)

    # 模拟进程重启后 Docker 里仍有 running 的 cached 用户容器，但当前进程
    # 没有任何活跃 lease；这时不应再被容器名永久阻塞。
    removed = user_data_cleanup.cleanup_expired_user_data(
        active_user_ids=set(),
        active_container_names=["erp-sandbox-alice-abc123"],
        uploads_ttl_days=30,
        outputs_ttl_days=30,
    )

    assert removed == 1
    assert not old.exists()

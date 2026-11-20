"""上传接口：路径安全、大小限制、列表与删除。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import uploads as uploads_router
from erpflow.backends import user_data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    monkeypatch.setattr(uploads_router, "MAX_UPLOAD_BYTES", 1024 * 1024)
    monkeypatch.setattr(uploads_router, "MAX_USER_UPLOAD_BYTES", 10 * 1024 * 1024)
    app = FastAPI()
    app.include_router(uploads_router.router)
    return TestClient(app), tmp_path


def test_upload_list_delete_roundtrip(client):
    http, tmp_path = client
    res = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("report.txt", b"hello", "text/plain"))],
    )
    assert res.status_code == 200, res.text
    assert res.json()["files"][0]["filename"] == "report.txt"

    target = tmp_path / "users" / user_data.user_data_dir_name("alice") / "user-data" / "uploads" / "report.txt"
    assert target.read_bytes() == b"hello"

    listed = http.get("/api/uploads", params={"user_id": "alice"})
    assert listed.status_code == 200
    assert [f["filename"] for f in listed.json()["files"]] == ["report.txt"]

    deleted = http.delete("/api/uploads/report.txt", params={"user_id": "alice"})
    assert deleted.status_code == 200
    assert not target.exists()


def test_duplicate_filename_gets_unique_suffix(client):
    http, _ = client
    files = [("files", ("same.txt", b"one", "text/plain"))]
    first = http.post("/api/uploads", data={"user_id": "bob"}, files=files)
    second = http.post("/api/uploads", data={"user_id": "bob"}, files=files)
    assert first.status_code == 200
    assert second.status_code == 200
    names = [first.json()["files"][0]["filename"], second.json()["files"][0]["filename"]]
    assert len(set(names)) == 2
    assert names[1].startswith("same-")


def test_unsafe_filename_rejected(client):
    http, _ = client
    res = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("..\\evil.txt", b"x", "text/plain"))],
    )
    assert res.status_code == 400
    assert "文件名非法" in res.text


def test_size_limit_rejected(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(uploads_router, "MAX_UPLOAD_BYTES", 3)
    res = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("big.txt", b"1234", "text/plain"))],
    )
    assert res.status_code == 413


def test_empty_file_rejected(client):
    http, _ = client
    res = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("empty.txt", b"", "text/plain"))],
    )
    assert res.status_code == 400

def test_user_quota_rejected(client, monkeypatch):
    http, _ = client
    monkeypatch.setattr(uploads_router, "MAX_USER_UPLOAD_BYTES", 5)

    first = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("a.txt", b"1234", "text/plain"))],
    )
    assert first.status_code == 200

    second = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("b.txt", b"1234", "text/plain"))],
    )
    assert second.status_code == 413
    assert "用户上传空间" in second.text


def test_part_filename_rejected(client):
    """用户不能用 .part 作为最终文件名绕过配额/列表过滤。"""
    http, tmp_path = client
    res = http.post(
        "/api/uploads",
        data={"user_id": "alice"},
        files=[("files", ("hidden.part", b"x", "application/octet-stream"))],
    )
    assert res.status_code == 400
    assert ".part" in res.text
    assert not (tmp_path / "users" / user_data.user_data_dir_name("alice") / "user-data" / "uploads" / "hidden.part").exists()

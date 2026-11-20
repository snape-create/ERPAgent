"""gateway /api/artifacts: 直接提供 user-data outputs，并保留旧下载路由。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.gateway.app import create_app
from erpflow.backends import user_data


def test_artifacts_route_serves_user_output(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    outputs = user_data.outputs_dir("alice")
    outputs.mkdir(parents=True)
    (outputs / "report.html").write_text("<html>ok</html>", encoding="utf-8")

    client = TestClient(create_app())
    response = client.get("/api/artifacts/alice/report.html")

    assert response.status_code == 200
    assert response.text == "<html>ok</html>"
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-disposition"].startswith("inline")


def test_artifacts_route_force_download(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    outputs = user_data.outputs_dir("alice")
    outputs.mkdir(parents=True)
    (outputs / "report.html").write_text("<html>ok</html>", encoding="utf-8")

    client = TestClient(create_app())
    response = client.get("/api/artifacts/alice/report.html?download=1")

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


def test_artifacts_route_is_user_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    outputs = user_data.outputs_dir("alice")
    outputs.mkdir(parents=True)
    (outputs / "report.html").write_text("alice", encoding="utf-8")

    client = TestClient(create_app())
    assert client.get("/api/artifacts/alice/report.html").status_code == 200
    assert client.get("/api/artifacts/bob/report.html").status_code == 404


def test_artifacts_route_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    client = TestClient(create_app())

    response = client.get("/api/artifacts/alice/%2E%2E/secret.txt")

    assert response.status_code == 400
    assert "路径非法" in response.text

"""download_sandbox_file：outputs 直接走 artifact URL，不再复制到 download。"""
from __future__ import annotations

from erpflow.backends import user_data


def test_download_outputs_returns_artifact_url_without_copy(monkeypatch, tmp_path):
    from erpflow.tools import download_sandbox_file as module

    monkeypatch.setattr(user_data, "SANDBOX_DATA_ROOT", tmp_path)
    outputs = user_data.outputs_dir("alice")
    outputs.mkdir(parents=True)
    (outputs / "report.html").write_text("<html>ok</html>", encoding="utf-8")

    download_dir = tmp_path / "download"
    monkeypatch.setattr(module, "DOWNLOAD_DIR", download_dir)
    monkeypatch.setattr(module, "SANDBOX_USER_DATA_MOUNTS", True)
    monkeypatch.setattr(module, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(
        module,
        "_get_docker_container",
        lambda remote_path="": (_ for _ in ()).throw(AssertionError("must not touch Docker")),
    )

    result = module.download_sandbox_file.func("/mnt/user-data/outputs/report.html")

    assert "/api/artifacts/alice/report.html" in result
    assert "下载链接" in result
    assert "本地路径" not in result
    assert not (download_dir / "report.html").exists()

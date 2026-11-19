"""路径安全回归测试（P0-3 路径穿越 / P0-4 不安全的本地回退）。

覆盖三层：
- 纯函数 ``safe_filename`` / ``resolve_under``
- ``download_sandbox_file`` 在沙箱不可用时的行为
- ``generate_document`` 对文件名的校验
"""
import pytest

from erpflow.tools.path_safety import UnsafePathError, resolve_under, safe_filename

ACCEPTED_NAMES = ["report.md", "a.txt", "中文 报告.md", "x-1_2.json"]

REJECTED_NAMES = ["../x", "a/b", "a\\b", "/etc/passwd", "C:\\x", "", ".", ".."]


# ---------------------------------------------------------------- safe_filename


@pytest.mark.parametrize("name", ACCEPTED_NAMES)
def test_safe_filename_accepts_plain_names(name):
    assert safe_filename(name) == name


@pytest.mark.parametrize("name", REJECTED_NAMES)
def test_safe_filename_rejects_unsafe_names(name):
    with pytest.raises(UnsafePathError):
        safe_filename(name)


def test_safe_filename_rejects_non_string():
    with pytest.raises(UnsafePathError):
        safe_filename(None)
    with pytest.raises(UnsafePathError):
        safe_filename(123)


def test_safe_filename_rejects_nul_byte():
    with pytest.raises(UnsafePathError):
        safe_filename("a\x00b")


def test_safe_filename_rejects_windows_drive_prefix():
    with pytest.raises(UnsafePathError):
        safe_filename("C:x")


# ---------------------------------------------------------------- resolve_under


def test_resolve_under_accepts_normal_file(tmp_path):
    assert resolve_under(tmp_path, "report.md") == (tmp_path / "report.md").resolve()


@pytest.mark.parametrize("name", ["../x", "/etc/passwd", "a/b", "..", "."])
def test_resolve_under_rejects_traversal_and_absolute(tmp_path, name):
    with pytest.raises(UnsafePathError):
        resolve_under(tmp_path, name)


def test_resolve_under_rejects_symlink_escape(tmp_path):
    base = tmp_path / "download"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    link = base / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")

    with pytest.raises(UnsafePathError):
        resolve_under(base, "link.txt")


# -------------------------------------------------- download_sandbox_file


def _patch_download_dir(monkeypatch, tmp_path):
    """把工具的 DOWNLOAD_DIR 指向临时目录，返回 (模块, 目录)。"""
    from erpflow.tools import download_sandbox_file as module

    download_dir = tmp_path / "download"
    download_dir.mkdir()
    monkeypatch.setattr(module, "DOWNLOAD_DIR", download_dir)
    # 模拟沙箱不可用
    monkeypatch.setattr(module, "_get_docker_container", lambda remote_path="": (None, None))
    return module, download_dir


def test_no_local_fallback_when_disabled(monkeypatch, tmp_path):
    """P0-4：默认关闭回退，沙箱不可用时不得读取宿主机文件。"""
    module, download_dir = _patch_download_dir(monkeypatch, tmp_path)
    host_secret = tmp_path / "host_secret.txt"
    host_secret.write_text("top secret", encoding="utf-8")
    monkeypatch.setattr(module, "SANDBOX_ALLOW_LOCAL_FALLBACK", False)

    result = module.download_sandbox_file.func(str(host_secret), "copied.txt")

    assert "文件下载失败" in result
    assert "top secret" not in result
    assert not (download_dir / "copied.txt").exists()


def test_local_fallback_only_reads_inside_download_dir(monkeypatch, tmp_path):
    """P0-4：开启回退后，DOWNLOAD_DIR 内的文件仍可正常导出。"""
    module, download_dir = _patch_download_dir(monkeypatch, tmp_path)
    source = download_dir / "report.md"
    source.write_text("hello", encoding="utf-8")
    monkeypatch.setattr(module, "SANDBOX_ALLOW_LOCAL_FALLBACK", True)

    result = module.download_sandbox_file.func(str(source), "out.md")

    assert "文件已导出" in result
    assert (download_dir / "out.md").read_text(encoding="utf-8") == "hello"


def test_local_fallback_rejects_path_outside_download_dir(monkeypatch, tmp_path):
    """P0-4：即使开启回退，DOWNLOAD_DIR 之外的文件也必须拒绝。"""
    module, download_dir = _patch_download_dir(monkeypatch, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("top secret", encoding="utf-8")
    monkeypatch.setattr(module, "SANDBOX_ALLOW_LOCAL_FALLBACK", True)

    result = module.download_sandbox_file.func(str(outside), "stolen.txt")

    assert "路径非法" in result
    assert not (download_dir / "stolen.txt").exists()


def test_download_rejects_traversal_filename(monkeypatch, tmp_path):
    """P0-3：filename 参数不能穿越出 DOWNLOAD_DIR。"""
    module, download_dir = _patch_download_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "SANDBOX_ALLOW_LOCAL_FALLBACK", False)

    result = module.download_sandbox_file.func("whatever.md", "../evil.md")

    assert "文件名非法" in result
    assert not (tmp_path / "evil.md").exists()
    assert not (download_dir / "evil.md").exists()


# ------------------------------------------------------ generate_document


def test_generate_document_rejects_unsafe_filename(monkeypatch, tmp_path):
    from erpflow.tools import document_generator as module

    monkeypatch.setattr(module, "LOCAL_DOWNLOAD_DIR", tmp_path)

    result = module.generate_document.func(
        title="报告",
        content="内容",
        format="markdown",
        filename="../evil",
    )

    assert "文件名非法" in result
    assert not (tmp_path.parent / "evil.md").exists()

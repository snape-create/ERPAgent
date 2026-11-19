"""web_fetch 的 SSRF 防护与压缩包解压上限测试（全部离线）。"""
import io
import socket
import zipfile

import pytest

from erpflow.tools import web_fetch as wf


def _make_zip(entries: dict[str, bytes]) -> bytes:
    """在内存中构造 ZIP，避免任何磁盘/网络访问。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _fake_getaddrinfo(ip: str):
    def _resolver(host, port, *args, **kwargs):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    return _resolver


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
    ],
)
def test_validate_url_rejects_internal_targets(url):
    with pytest.raises(ValueError):
        wf._validate_url(url)


def test_validate_url_allows_private_when_enabled(monkeypatch):
    monkeypatch.setattr(wf, "SANDBOX_ALLOW_PRIVATE_WEB", True)

    assert wf._validate_url("http://127.0.0.1/") == "http://127.0.0.1/"
    assert wf._validate_url("http://localhost/") == "http://localhost/"


def test_validate_url_rejects_hostname_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(wf.socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.5"))

    with pytest.raises(ValueError, match="内网"):
        wf._validate_url("http://evil.example.com/payload")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file.zip",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com/",
    ],
)
def test_validate_url_rejects_non_http_scheme(url):
    with pytest.raises(ValueError):
        wf._validate_url(url)


def test_archive_rejects_traversal_member():
    data = _make_zip({"skill/../evil.txt": b"pwned"})

    with pytest.raises(ValueError):
        wf._extract_archive_to_memory(data, "http://example.com/skill.zip", "application/zip")


def test_archive_rejects_absolute_member():
    data = _make_zip({"/etc/evil.txt": b"pwned"})

    with pytest.raises(ValueError):
        wf._extract_archive_to_memory(data, "http://example.com/skill.zip", "application/zip")


def test_archive_rejects_too_many_files(monkeypatch):
    monkeypatch.setattr(wf, "_MAX_ARCHIVE_FILES", 2)
    data = _make_zip({f"skill/file{i}.txt": b"x" for i in range(3)})

    with pytest.raises(ValueError, match="数量"):
        wf._extract_archive_to_memory(data, "http://example.com/skill.zip", "application/zip")


def test_archive_rejects_oversized_extraction(monkeypatch):
    monkeypatch.setattr(wf, "_MAX_EXTRACTED_BYTES", 10)
    data = _make_zip({"skill/big.txt": b"x" * 100})

    with pytest.raises(ValueError, match="大小"):
        wf._extract_archive_to_memory(data, "http://example.com/skill.zip", "application/zip")


def test_archive_normal_zip_extracts():
    data = _make_zip(
        {
            "skill/SKILL.md": b"# demo",
            "skill/scripts/run.py": b"print('hi')",
            "skill/assets/": b"",
        }
    )

    files = wf._extract_archive_to_memory(
        data, "http://example.com/skill.zip", "application/zip"
    )

    assert files == {
        "SKILL.md": b"# demo",
        "scripts/run.py": b"print('hi')",
    }

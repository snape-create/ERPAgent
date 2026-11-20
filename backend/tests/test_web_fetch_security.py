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


class _FakeResponse:
    def __init__(self, url, status_code=200, *, location=None, body=b"", content_type="text/plain"):
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        if location:
            self.headers["location"] = location
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url):
        self.requests.append(url)
        return self.responses.pop(0)


def test_http_get_validates_redirect_before_request(monkeypatch):
    fake = _FakeClient([
        _FakeResponse("http://public.example/start", 302, location="http://internal.example/secret"),
    ])
    seen = []

    def validate(url):
        seen.append(url)
        if "internal.example" in url:
            raise ValueError("blocked internal redirect")
        return url

    monkeypatch.setattr(wf, "_validate_url", validate)
    monkeypatch.setattr(wf.httpx, "Client", lambda **kwargs: fake)

    with pytest.raises(ValueError, match="blocked"):
        wf._http_get_limited("http://public.example/start", 100)

    # 关键：只请求了公网起点，没有请求被拦截的 Location。
    assert fake.requests == ["http://public.example/start"]
    assert "http://internal.example/secret" in seen


def test_http_get_follows_valid_redirect(monkeypatch):
    fake = _FakeClient([
        _FakeResponse("http://public.example/start", 302, location="/next"),
        _FakeResponse("http://public.example/next", 200, body=b"ok"),
    ])
    monkeypatch.setattr(wf, "_validate_url", lambda url: url)
    monkeypatch.setattr(wf.httpx, "Client", lambda **kwargs: fake)

    body, content_type, final_url = wf._http_get_limited("http://public.example/start", 100)

    assert body == b"ok"
    assert content_type == "text/plain"
    assert final_url == "http://public.example/next"
    assert fake.requests == [
        "http://public.example/start",
        "http://public.example/next",
    ]


class _FakeDependencySandbox:
    def __init__(self, *, has_requirements=True, exit_code=0, output=""):
        self.has_requirements = has_requirements
        self.exit_code = exit_code
        self.output = output
        self.writes = []
        self.commands = []

    def file_exists(self, path):
        return self.has_requirements

    def read_file_bytes(self, path):
        return b"numpy==2.0.0\n"

    def write_file(self, path, content):
        self.writes.append((path, content))

    def execute(self, command, timeout=None):
        self.commands.append(command)
        from types import SimpleNamespace

        return SimpleNamespace(exit_code=self.exit_code, output=self.output)


def test_skill_dependency_status_skipped_without_requirements():
    sandbox = _FakeDependencySandbox(has_requirements=False)

    status = wf._install_dependencies_in_sandbox(sandbox, "/tmp/skill", "demo")

    assert status == "skipped"
    assert sandbox.commands == []


def test_skill_dependency_status_installed():
    sandbox = _FakeDependencySandbox(exit_code=0)

    status = wf._install_dependencies_in_sandbox(sandbox, "/tmp/skill", "demo")

    assert status == "installed"
    assert any("pip install" in command for command in sandbox.commands)


def test_skill_dependency_status_surfaces_pip_failure():
    sandbox = _FakeDependencySandbox(
        exit_code=1,
        output="No matching distribution found for numpy\n(retry later)",
    )

    status = wf._install_dependencies_in_sandbox(sandbox, "/tmp/skill", "demo")

    assert status.startswith("failed:")
    assert "No matching distribution" in status
    assert "\n" not in status

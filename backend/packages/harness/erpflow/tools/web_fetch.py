"""
Web 抓取 & Skill 下载工具
- web_fetch: 获取任意 URL 的文本内容（HTML→纯文本 / JSON / Markdown）
- install_skill: 从 URL 下载完整 Skill 文件夹（含 SKILL.md + 脚本 + 依赖）并安装。

Skill 标准结构：
  skill-name/
    SKILL.md          # 技能定义（必须有）
    scraper.py        # 脚本文件（可选）
    requirements.txt  # 依赖（可选）
    assets/           # 资源文件（可选）
"""
import base64
import io
import ipaddress
import re
import socket
import tarfile
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from langchain_core.tools import tool

from ..config import (
    SANDBOX_ALLOW_PRIVATE_WEB,
    SANDBOX_MAX_ARCHIVE_FILES,
    SANDBOX_MAX_ARCHIVE_MB,
    SANDBOX_MAX_EXTRACTED_MB,
    SANDBOX_MAX_FETCH_MB,
    SANDBOX_PYTHON_PACKAGES_DIR,
    skills_store_namespace,
)
from ..log_utils import agent_logger
from ..runtime_context import get_current_store, get_current_user_id

# 下载/解压上限（统一换算成字节，便于流式累加比较）
_MAX_FETCH_BYTES = SANDBOX_MAX_FETCH_MB * 1024 * 1024
_MAX_ARCHIVE_BYTES = SANDBOX_MAX_ARCHIVE_MB * 1024 * 1024
_MAX_EXTRACTED_BYTES = SANDBOX_MAX_EXTRACTED_MB * 1024 * 1024
_MAX_ARCHIVE_FILES = SANDBOX_MAX_ARCHIVE_FILES

_FETCH_HEADERS = {"User-Agent": "ERP-Agent/1.0 (Skill Downloader)"}


class _HttpStatusError(Exception):
    """非 200 响应：保留调用方原有的错误文案。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """内网 / 本机 / 链路本地 / 组播 / 保留 / 未指定地址一律视为不可访问。"""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_url(url: str) -> str:
    """校验 URL 协议与目标地址，阻止 SSRF。

    仅允许 http/https；域名解析出的所有 IP 都必须是公网地址。
    显式开启 SANDBOX_ALLOW_PRIVATE_WEB 时放行内网/本机地址（开发调试用）。
    DNS rebinding 在本层无法完全防御，属于可接受的残余风险。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL 协议不支持，仅允许 http/https: {url}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL 缺少有效主机名: {url}")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"URL 端口无效: {url}") from exc
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析主机名: {host}") from exc
    if not infos:
        raise ValueError(f"无法解析主机名: {host}")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _ip_is_blocked(ip):
            continue
        if SANDBOX_ALLOW_PRIVATE_WEB and (ip.is_private or ip.is_loopback):
            continue
        raise ValueError(f"安全限制：禁止访问内网或保留地址 {ip}（主机 {host}）")

    return url


_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def _http_get_limited(
    url: str, max_bytes: int, timeout: float = 30.0
) -> tuple[bytes, str, str]:
    """带大小上限的 HTTP GET，返回 (内容字节, content-type, 最终 URL)。

    重定向必须手动逐跳处理：httpx 的 ``follow_redirects=True`` 会先请求
    Location 目标，再让调用方看到最终 URL；此时内部地址已经被访问过了。
    这里每拿到一个 Location，先 ``_validate_url``，通过后才发下一跳。
    """
    current_url = _validate_url(url)

    with httpx.Client(
        follow_redirects=False,
        timeout=timeout,
        headers=_FETCH_HEADERS,
    ) as client:
        for redirect_count in range(_MAX_REDIRECTS + 1):
            with client.stream("GET", current_url) as response:
                if response.status_code in _REDIRECT_CODES:
                    if redirect_count >= _MAX_REDIRECTS:
                        raise ValueError(f"重定向次数超过限制: {url}")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError(
                            f"重定向响应缺少 Location（HTTP {response.status_code}）"
                        )
                    next_url = urljoin(current_url, location)
                    # 必须在发起下一跳请求之前校验，不能等请求完再校验。
                    current_url = _validate_url(next_url)
                    continue

                if response.status_code != 200:
                    raise _HttpStatusError(response.status_code)

                final_url = str(response.url)
                # 防御性复核：确保实际请求 URL 仍是校验通过的那个。
                _validate_url(final_url)
                content_type = response.headers.get("content-type", "")

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        limit_mb = max_bytes // (1024 * 1024)
                        raise ValueError(
                            f"下载内容超过大小限制（最大 {limit_mb} MB）: {final_url}"
                        )
                    chunks.append(chunk)

                return b"".join(chunks), content_type, final_url

    # loop 正常只会从 return 退出；保留兜底让类型检查器知道不会落空。
    raise ValueError(f"重定向次数超过限制: {url}")


def _strip_html(html: str) -> str:
    """简易 HTML → 纯文本（去标签、合并空白）"""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@tool
def web_fetch(url: str, max_chars: int = 8000) -> str:
    """获取指定 URL 的网页内容（转为纯文本）。

    可用于：
    - 查看技术文档、API 说明
    - 获取 Skill 文件内容
    - 阅读在线资源

    Args:
        url: 完整的 HTTP/HTTPS 地址
        max_chars: 返回内容最大字符数（默认8000）

    Returns:
        网页文本内容或错误信息
    """
    if not url.startswith(("http://", "https://")):
        return "错误: URL 必须以 http:// 或 https:// 开头"

    try:
        content, content_type, _ = _http_get_limited(url, _MAX_FETCH_BYTES, timeout=20)

        text = content.decode("utf-8", errors="replace")

        # HTML → 纯文本
        if "text/html" in content_type:
            text = _strip_html(text)

        # 截断
        total_chars = len(text)
        if total_chars > max_chars:
            text = text[:max_chars] + f"\n\n... [内容已截断，共 {total_chars} 字符]"

        agent_logger.info(f"web_fetch: {url} ({len(text)} chars)")
        return text

    except _HttpStatusError as e:
        return f"请求失败: HTTP {e.status_code}"
    except ValueError as e:
        return str(e)
    except httpx.TimeoutException:
        return f"请求超时: {url}"
    except Exception as e:
        agent_logger.error(f"web_fetch error: {e}")
        return f"获取失败: {str(e)}"


# ============================================================
# Skill 安装（沙箱安全隔离）
# 流程图：下载 → 沙箱验证 → 通过 → 安装到正式环境
#         ↓ 失败
#       清理沙箱临时文件 + 返回错误
# ============================================================
def _validate_archive_member_name(name: str) -> None:
    """拒绝绝对路径、反斜杠、NUL 与 '..' 路径段的压缩包成员。"""
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"压缩包成员名称非法: {name!r}")
    if PurePosixPath(name).is_absolute() or re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"压缩包成员使用了绝对路径: {name}")
    if ".." in PurePosixPath(name).parts:
        raise ValueError(f"压缩包成员包含路径上溯: {name}")


def _validate_relative_member_path(rel_path: str, original: str) -> None:
    """校验剥离顶层目录后的相对路径仍然安全。"""
    if "\x00" in rel_path or "\\" in rel_path:
        raise ValueError(f"压缩包成员名称非法: {original!r}")
    path = PurePosixPath(rel_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"压缩包成员路径不安全: {original}")


def _check_member_size(size: int, name: str) -> None:
    if size > _MAX_EXTRACTED_BYTES:
        limit_mb = _MAX_EXTRACTED_BYTES // (1024 * 1024)
        raise ValueError(f"压缩包内单个文件超过大小限制（最大 {limit_mb} MB）: {name}")


def _check_archive_limits(file_count: int, total_bytes: int) -> None:
    if file_count > _MAX_ARCHIVE_FILES:
        raise ValueError(f"压缩包文件数量超过限制（最大 {_MAX_ARCHIVE_FILES} 个）")
    if total_bytes > _MAX_EXTRACTED_BYTES:
        limit_mb = _MAX_EXTRACTED_BYTES // (1024 * 1024)
        raise ValueError(f"压缩包解压后总大小超过限制（最大 {limit_mb} MB）")


def _extract_archive_to_memory(data: bytes, url: str, content_type: str) -> dict[str, bytes]:
    """
    将压缩包解压到内存字典 {相对路径: 字节内容}。
    支持 ZIP 和 TAR.GZ 格式。

    安全约束：成员路径必须为安全的相对路径；文件数量、单文件大小与解压后
    总大小均有上限，超限直接抛 ValueError（由 install_skill 转为安装失败）。
    """
    # 初始化结果字典，用于存储解压后的文件路径和内容
    result: dict[str, bytes] = {}
    # 已读取（解压后）的累计字节数
    total_bytes = 0

    try:
        # 检查 URL 是否以 .zip 结尾或 content-type 包含 zip
        if url.lower().endswith(".zip") or "zip" in content_type:
            # 使用 io.BytesIO 将字节数据转为内存中的文件对象
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # 遍历 ZIP 包中的所有成员（文件/文件夹）
                for info in zf.infolist():
                    member = info.filename
                    # 先校验原始名称，绝对路径/上溯必须在任何读取之前拒绝
                    _validate_archive_member_name(member)
                    # 跳过目录、__MACOSX 系统文件夹、隐藏文件（以.开头）
                    if info.is_dir() or member.endswith("/") or "/__MACOSX/" in member or member.startswith("."):
                        continue
                    # 按第一个斜杠分割路径，获取相对路径
                    parts = member.split("/", 1)
                    # 如果有多级目录，取第二部分作为相对路径；否则直接用文件名
                    rel_path = parts[1] if len(parts) > 1 else parts[0]
                    if not rel_path:
                        continue
                    # 剥离顶层目录后仍需是安全相对路径
                    _validate_relative_member_path(rel_path, member)
                    # 先看声明大小，避免把超大成员读进内存
                    _check_member_size(info.file_size, member)
                    _check_archive_limits(len(result) + 1, total_bytes + info.file_size)
                    # 将文件内容读入内存，存入结果字典
                    content = zf.read(info)
                    total_bytes += len(content)
                    _check_archive_limits(len(result) + 1, total_bytes)
                    result[rel_path] = content
        else:
            # 处理 tar.gz 格式
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                # 遍历 tar 包中的所有成员
                for member in tf.getmembers():
                    _validate_archive_member_name(member.name)
                    # 只处理文件（不是目录），且文件名不以.开头
                    if not member.isfile() or member.name.startswith("."):
                        continue
                    # 按第一个斜杠分割，获取相对路径
                    parts = member.name.split("/", 1)
                    rel_path = parts[1] if len(parts) > 1 else parts[0]
                    if not rel_path:
                        continue
                    _validate_relative_member_path(rel_path, member.name)
                    _check_member_size(member.size, member.name)
                    _check_archive_limits(len(result) + 1, total_bytes + member.size)
                    # 提取文件内容
                    fobj = tf.extractfile(member)
                    if fobj:
                        content = fobj.read()
                        total_bytes += len(content)
                        _check_archive_limits(len(result) + 1, total_bytes)
                        result[rel_path] = content
    except (zipfile.BadZipFile, tarfile.TarError):
        # 如果压缩包损坏或格式不对，不做任何处理，返回空字典
        pass

    # 返回解压后的文件字典
    return result


# 允许的安全导入白名单（方案二：白名单机制）
_ALLOWED_IMPORTS = {
    'json', 're', 'datetime', 'collections', 'itertools',
    'math', 'random', 'typing', 'dataclasses', 'enum',
    'pathlib', 'os', 'io', 'csv',
}

# 危险模式检测列表
_DANGEROUS_PATTERNS = [
    (r'eval\s*\(', "使用了 eval()，可能执行任意代码"),
    (r'exec\s*\(', "使用了 exec()，可能执行任意代码"),
    (r'__import__\s*\(', "使用了动态导入，可能加载恶意模块"),
    (r'subprocess\.(Popen|call|run|check_output|check_call)', "使用了 subprocess，可能执行系统命令"),
    (r'os\.system\s*\(', "使用了 os.system，可能执行系统命令"),
    (r'os\.popen\s*\(', "使用了 os.popen，可能执行系统命令"),
    (r'builtins\s*\.', "访问了内置模块，可能有风险"),
    (r'compile\s*\(', "使用了 compile()，可能执行动态代码"),
    (r'pickle\.(loads|load)', "使用了 pickle 反序列化，可能有远程执行风险"),
    (r'socket\.', "使用了 socket 通信，可能有网络风险"),
    (r'ctypes\.', "使用了 ctypes，可能绕过 Python 安全限制"),
]


def _scan_python_file(content: str, filepath: str) -> list[str]:
    """扫描单个 Python 文件的安全风险（方案二部分）。
    返回: 警告列表（非空即有风险）
    """
    warnings = []

    for pattern, warning in _DANGEROUS_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            warnings.append(f"{filepath}: {warning}")

    return warnings


def _validate_skill_in_sandbox(sandbox, skill_name: str, sandbox_skill_dir: str) -> tuple[bool, str, list[str]]:
    """
    在沙箱中验证技能文件完整性（严格模式：方案一 + 方案二部分）。

    验证项目：
    1. SKILL.md 文件存在性
    2. YAML frontmatter 合法解析
    3. 必需字段 name 的非空校验
    4. 可选字段类型校验 (version, description)
    5. name 字段路径安全性校验
    6. Python 文件危险代码扫描（方案二）
    7. 路径穿越攻击检测
    8. 文件大小 DoS 防护

    返回: (是否通过, 错误信息, 文件列表)
    """
    # 1. 检查 SKILL.md 是否存在
    has_sk = sandbox.execute(f"test -f '{sandbox_skill_dir}/SKILL.md' && echo YES || echo NO")
    if has_sk.exit_code != 0 or "YES" not in has_sk.output:
        return False, "SKILL.md 未找到，技能格式无效", []

    # 2. 读取完整内容
    read_sk = sandbox.execute(f"cat '{sandbox_skill_dir}/SKILL.md'")
    if read_sk.exit_code != 0:
        return False, "无法读取 SKILL.md", []

    content = read_sk.output

    # 3. 验证 YAML frontmatter 是否存在且格式正确
    frontmatter_pattern = r'^---\s*\n(.*?)\n---\s*\n'
    match = re.search(frontmatter_pattern, content, re.DOTALL)

    if not match:
        return False, "SKILL.md 缺少有效的 YAML frontmatter（必须用 --- 包裹）", []

    # 4. 使用 yaml 库严格解析
    try:
        frontmatter_yaml = match.group(1)
        metadata = yaml.safe_load(frontmatter_yaml)

        if not isinstance(metadata, dict):
            return False, "YAML frontmatter 格式错误（必须是键值对）", []

        if "name" not in metadata:
            return False, "SKILL.md frontmatter 缺少必需的 'name' 字段", []

        if not metadata["name"] or not isinstance(metadata["name"], str):
            return False, "SKILL.md 'name' 字段必须是非空字符串", []

        if "version" in metadata and not isinstance(metadata["version"], str):
            return False, "SKILL.md 'version' 字段必须是字符串", []

        if "description" in metadata and not isinstance(metadata["description"], str):
            return False, "SKILL.md 'description' 字段必须是字符串", []

        if not re.match(r'^[\w\-\.]+$', metadata["name"]):
            return False, f"SKILL.md 'name' 字段包含非法字符: {metadata['name']}", []

        if metadata["name"] != skill_name:
            agent_logger.warning(
                f"Skill name mismatch: SKILL.md name='{metadata['name']}' != URL name='{skill_name}'"
            )

    except yaml.YAMLError as e:
        return False, f"YAML frontmatter 解析失败: {str(e)}", []

    # 5. 安全扫描：检查 Python 文件中的危险模式（方案二部分）
    py_files_result = sandbox.execute(
        f"find '{sandbox_skill_dir}' -name '*.py' -type f 2>/dev/null"
    )
    if py_files_result.exit_code == 0 and py_files_result.output.strip():
        for py_file in py_files_result.output.strip().split('\n'):
            if not py_file.strip():
                continue
            file_content = sandbox.execute(f"cat '{py_file}'")
            if file_content.exit_code != 0:
                continue
            warnings = _scan_python_file(file_content.output, py_file)
            if warnings:
                for w in warnings:
                    agent_logger.warning(f"Security risk in skill: {w}")

    # 6. 路径穿越攻击检测 & 文件大小检查
    ls_result = sandbox.execute(f"find '{sandbox_skill_dir}' -type f | sort")
    files = []
    if ls_result.exit_code == 0:
        for f in ls_result.output.strip().split('\n'):
            f = f.strip()
            if not f:
                continue
            if '..' in f:
                return False, f"检测到路径穿越攻击: {f}", []
            files.append(f)

    # 7. 文件大小检查（DoS 防护）
    for f in files:
        size_check = sandbox.execute(
            f"stat -c%s '{f}' 2>/dev/null || stat -f%z '{f}' 2>/dev/null"
        )
        if size_check.exit_code == 0 and size_check.output.strip():
            try:
                file_size = int(size_check.output.strip())
                if file_size > 10 * 1024 * 1024:
                    return False, f"文件过大 ({file_size} bytes): {f}", []
            except ValueError:
                pass

    return True, "", files


def _relative_installed_files(sandbox_skill_dir: str, files: list[str]) -> list[str]:
    """把沙箱内绝对文件路径转换成相对 Skill 根目录的路径。

    这里不再写宿主机全局 ``backend/skills``：那会让一个用户安装的 Skill
    被 SkillsSync 同步进所有用户的沙箱。真正持久化由用户级 Store 完成。
    """
    prefix = sandbox_skill_dir.rstrip("/") + "/"
    installed: list[str] = []
    for remote_path in files:
        if remote_path.startswith(prefix):
            rel = remote_path[len(prefix):]
        else:
            rel = remote_path.replace(sandbox_skill_dir, "").lstrip("/")
        parts = rel.split("/")
        if not rel or any(part in ("", ".", "..") for part in parts):
            continue
        installed.append(rel)
    return installed


def _persist_installed_skill(
    sandbox,
    sandbox_skill_dir: str,
    skill_name: str,
    files: list[str],
    source_url: str,
) -> int:
    """把已验证的 Skill 文件写入当前用户的 Store，供新沙箱恢复。

    该函数读取请求级 ContextVar，因此同一个 ``install_skill`` 工具对象在不同
    用户并发调用时也不会把文件写到彼此的 namespace。
    """
    store = get_current_store()
    if store is None:
        return 0

    user_id = get_current_user_id()
    persisted = 0
    for rel in files:
        try:
            normalized = rel.replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
                raise ValueError("unsafe relative path")

            content = sandbox.read_file_bytes(f"{sandbox_skill_dir}/{path.as_posix()}")
            key = f"{skill_name}/{path.as_posix()}"
            store.put(
                skills_store_namespace(user_id),
                key,
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                    "source": source_url,
                },
            )
            persisted += 1
        except Exception as exc:
            # 已通过验证的文件有一条落库失败时仍保留本次安装结果，但明确记录。
            agent_logger.warning(f"Failed to persist Skill file {rel}: {exc}")
    return persisted


@tool
def install_skill(url: str, skill_name: str = "") -> str:
    """从 URL 下载并安装完整 Skill（沙箱安全隔离流程）。

    安全流程：
    1. 下载到内存
    2. 上传到沙箱 /tmp/ 验证
    3. 在沙箱中解压并校验 SKILL.md 完整性
    4. 校验通过 → 从沙箱复制到宿主机 backend/skills/
    5. 校验失败 → 清理沙箱临时文件

    支持四种来源：
    1. GitHub 仓库 ZIP（如 https://github.com/user/repo/archive/refs/heads/main.zip）
    2. 任意 ZIP/TAR.GZ 压缩包（包含 SKILL.md 的文件夹）
    3. 单个 SKILL.md 文件 URL
    4. GitHub 仓库页面（自动转换为 ZIP 下载）

    标准 Skill 文件夹结构：
      skill-name/
        SKILL.md          # 技能定义（必须）
        *.py              # 脚本（可选）
        requirements.txt  # 依赖（可选）

    Args:
        url: Skill 的下载地址（ZIP 压缩包、GitHub 仓库、或 .md 文件）
        skill_name: 技能名称。为空则从 URL 自动提取。

    Returns:
        安装结果说明（含安装路径和文件列表）
    """
    # 验证 URL 协议与目标地址（阻止 SSRF）
    try:
        _validate_url(url)
    except ValueError as e:
        return f"错误: {str(e)}"

    # 标准化 GitHub URL，将仓库页面转为 ZIP 下载链接
    url = _normalize_github_url(url)
    # 如果未指定技能名称，从 URL 自动提取
    if not skill_name:
        skill_name = _extract_skill_name(url)
    # 将技能名称中的非法字符替换为下划线，确保文件系统安全
    skill_name = re.sub(r"[^\w\-]", "_", skill_name)

    # 获取沙箱实例（安全隔离环境）
    from ..backends.sandbox_holder import get_sandbox
    sandbox = get_sandbox()

    try:
        # Step 1: 下载文件到内存（带大小上限 + 重定向 SSRF 校验）
        data, content_type, _ = _http_get_limited(url, _MAX_ARCHIVE_BYTES, timeout=30)
        # 检查下载内容是否为空
        if len(data) == 0:
            return "错误: 下载内容为空"

        # 检查沙箱是否可用
        if sandbox is None:
            return "错误: 沙箱不可用，无法安全安装 Skill"

        # Step 2: 在 Python 中解压，获取文件映射
        # 在沙箱中创建临时目录用于验证
        sandbox_tmp = f"/tmp/_skill_install_{skill_name}"

        # 判断是否为压缩包格式
        if _is_archive(url, content_type):
            # 解压到内存字典
            files_dict = _extract_archive_to_memory(data, url, content_type)
        else:
            # 单文件（SKILL.md），直接保存为字节
            files_dict = {"SKILL.md": data}

        # 如果没有提取到任何文件，清理沙箱临时目录并报错
        if not files_dict:
            sandbox.execute(f"rm -rf {sandbox_tmp} 2>/dev/null || true")
            return "错误: 压缩包中未找到有效文件"

        # 找到 SKILL.md 所在的目录前缀（用于剥离顶层目录）
        skill_prefix = ""
        for path in files_dict:
            if path.endswith("SKILL.md"):
                # 如果路径包含斜杠，取目录部分作为前缀
                parts = path.split("/")
                if len(parts) > 1:
                    skill_prefix = "/".join(parts[:-1]) + "/"
                break
        else:
            # 如果循环结束都没找到 SKILL.md
            sandbox.execute(f"rm -rf {sandbox_tmp} 2>/dev/null || true")
            return "错误: 未找到 SKILL.md，安装取消"

        # Step 3: 上传文件到沙箱验证
        sandbox.execute(f"mkdir -p {sandbox_tmp}/skill")
        for rel_path, content in files_dict.items():
            clean_path = rel_path[len(skill_prefix):] if rel_path.startswith(skill_prefix) else rel_path
            if not clean_path:
                continue
            sandbox.write_file(f"{sandbox_tmp}/skill/{clean_path}", content)

        # Step 4: 在沙箱中验证文件完整性
        valid, err_msg, sandbox_files = _validate_skill_in_sandbox(sandbox, skill_name, f"{sandbox_tmp}/skill")
        if not valid:
            # 验证失败，清理沙箱临时文件
            sandbox.execute(f"rm -rf {sandbox_tmp}")
            return f"沙箱验证失败: {err_msg}（已清理沙箱临时文件，未影响宿主机）"

        # Step 5: 验证通过 → 写入当前用户的沙箱正式目录 + 用户级 Store
        sandbox_verify_dir = f"{sandbox_tmp}/skill"
        installed = _relative_installed_files(sandbox_verify_dir, sandbox_files)

        # 同步写入当前用户的正式目录；恢复器也使用同一 /skills/custom/<name>/ 协议。
        for rel in installed:
            remote_target = f"/skills/custom/{skill_name}/{rel}"
            try:
                sandbox.write_file(remote_target, sandbox.read_file_bytes(f"{sandbox_verify_dir}/{rel}"))
            except Exception as e:
                agent_logger.warning(f"Failed to write to sandbox skills dir: {e}")

        # 落到用户级 Store 后，容器被重建或服务重启仍可恢复同一套文件。
        persisted_count = _persist_installed_skill(
            sandbox=sandbox,
            sandbox_skill_dir=sandbox_verify_dir,
            skill_name=skill_name,
            files=installed,
            source_url=url,
        )

        # 安装依赖（在沙箱内执行 pip install -r requirements.txt）
        dependency_status = _install_dependencies_in_sandbox(
            sandbox, sandbox_verify_dir, skill_name
        )

        # 清理沙箱临时文件（释放临时空间）
        sandbox.execute(f"rm -rf {sandbox_tmp}")

        # 记录安装日志
        agent_logger.info(
            f"Skill installed (sandbox-verified): {skill_name} ({len(installed)} files)"
        )
        # 返回成功信息，包含技能名称、路径、文件数量和列表
        file_listing = "\n".join(f"  - {file_name}" for file_name in installed[:20])
        truncated_hint = (
            f"\n  ... 共 {len(installed)} 个文件" if len(installed) > 20 else ""
        )
        persisted_hint = (
            f"已持久化: {persisted_count} 个文件（当前用户）\n"
            if persisted_count
            else ""
        )
        dependency_hint = {
            "skipped": "依赖: 无 requirements.txt，跳过安装\n",
            "installed": "依赖: 已安装到 /opt/pydeps\n",
        }.get(
            dependency_status,
            (
                f"⚠️ 依赖安装失败: {dependency_status.removeprefix('failed: ')}\n"
                "Skill 文件已保留，但缺少依赖，运行前需要修复。\n"
                "注意：沙箱默认 `network_mode=none`，需要联网安装的依赖应由镜像预热。\n"
            ),
        )
        return (
            f"✅ Skill 文件安装成功（沙箱安全验证通过）\n"
            f"名称: {skill_name}\n"
            f"路径: /skills/custom/{skill_name}（当前用户沙箱）\n"
            f"文件数: {len(installed)}\n"
            f"{persisted_hint}"
            f"{dependency_hint}"
            f"文件列表:\n{file_listing}{truncated_hint}\n"
            f"来源: {url}\n\n"
            "该 Skill 已通过沙箱安全验证，文件已安装到当前用户环境。"
        )

    except _HttpStatusError as e:
        return f"下载失败: HTTP {e.status_code}"
    except httpx.TimeoutException:
        # 处理下载超时异常
        return f"下载超时: {url}"
    except Exception as e:
        # 处理其他所有异常，记录错误日志
        agent_logger.error(f"install_skill error: {e}")
        # 尝试清理沙箱临时文件
        if sandbox:
            try:
                sandbox.execute(f"rm -rf /tmp/_skill_install_{skill_name} 2>/dev/null || true")
            except Exception:
                pass
        return f"安装失败: {str(e)}"


def _normalize_github_url(url: str) -> str:
    """将 GitHub 仓库页面 URL 自动转换为 ZIP 下载链接"""
    # 匹配 GitHub 仓库 URL 的正则表达式
    # 格式: https://github.com/owner/repo 或 https://github.com/owner/repo/tree/branch/path
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(/.+))?",
        url,
    )
    if match:
        # 提取所有者、仓库名和分支
        owner, repo = match.group(1), match.group(2)
        branch = match.group(3) or "main"  # 默认使用 main 分支
        # 如果 URL 本身不是压缩包链接，转换为 ZIP 下载链接
        if not url.endswith((".zip", ".tar.gz", ".tgz")):
            return f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    return url  # 不是 GitHub 仓库 URL，原样返回


def _extract_skill_name(url: str) -> str:
    """从 URL 提取 skill 名称"""
    # 去掉查询参数（?后面的部分）
    path_part = url.split("?")[0].rstrip("/")

    # 如果是 GitHub 仓库，提取仓库名
    match = re.search(r"github\.com/[^/]+/([^/]+)", path_part)
    if match:
        return match.group(1).replace(".git", "")  # 移除 .git 后缀

    # 否则取路径的最后一部分作为文件名
    filename = path_part.split("/")[-1]
    # 移除常见的压缩包和文档扩展名
    name = re.sub(r"\.(zip|tar\.gz|tgz|md|txt|markdown)$", "", filename, flags=re.IGNORECASE)
    # 如果提取的名称为空，使用默认值
    return name or "downloaded_skill"


def _is_archive(url: str, content_type: str) -> bool:
    """判断是否为压缩包"""
    # 定义压缩包的扩展名列表
    archive_extensions = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tar")
    # 定义压缩包的 MIME 类型列表
    archive_types = (
        "application/zip",
        "application/x-zip-compressed",
        "application/gzip",
        "application/x-gzip",
        "application/x-tar",
        "application/x-bzip2",
        "application/octet-stream",
    )

    # 如果 URL 以压缩包扩展名结尾，判定为压缩包
    if any(url.lower().endswith(ext) for ext in archive_extensions):
        return True
    # 如果 content-type 匹配压缩包类型
    if any(t in content_type for t in archive_types):
        # 但是要排除 .md 或 .txt 单文件的情况（避免误判）
        if ".md" in url.lower() or ".txt" in url.lower():
            return False
        return True
    return False


def _install_dependencies_in_sandbox(
    sandbox, sandbox_skill_dir: str, skill_name: str
) -> str:
    """安装 Skill 的 Python 依赖到沙箱可执行依赖目录。

    Returns:
        ``skipped`` / ``installed`` / ``failed: <reason>``。调用方必须把失败
        状态返回给用户，不能把“文件已装、依赖缺失”静默报告成完全成功。
    """
    req_path = f"{sandbox_skill_dir}/requirements.txt"
    try:
        if not sandbox.file_exists(req_path):
            return "skipped"
        req_content = sandbox.read_file_bytes(req_path).decode("utf-8")
        sandbox_req = f"/tmp/requirements_{skill_name}.txt"
        sandbox.write_file(sandbox_req, req_content)
        result = sandbox.execute(
            f"mkdir -p {SANDBOX_PYTHON_PACKAGES_DIR} && "
            "python3 -m pip install --no-cache-dir "
            f"--target {SANDBOX_PYTHON_PACKAGES_DIR} "
            f"-r '{sandbox_req}' -q",
            timeout=120,
        )
        sandbox.execute(f"rm -f {sandbox_req}")
        if result.exit_code == 0:
            agent_logger.info(f"Skill dependencies installed in sandbox: {skill_name}")
            return "installed"
        output = (result.output or "").strip().replace("\n", " ")
        reason = output[:300] or f"pip exited with {result.exit_code}"
        agent_logger.warning(
            f"Sandbox dependency install failed for {skill_name}: {reason}"
        )
        return f"failed: {reason}"
    except Exception as e:
        agent_logger.warning(f"Sandbox dependency install error: {e}")
        return f"failed: {str(e)[:300]}"

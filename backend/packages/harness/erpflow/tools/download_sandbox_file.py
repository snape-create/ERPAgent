"""
沙箱文件下载工具（Harness — 真实 Docker 文件提取）
从沙箱容器中下载文件到宿主机 backend/download/ 目录，供用户通过 HTTP 访问。

工作原理：
1. 通过 Docker SDK 连接到 erp-sandbox 容器
2. 使用 base64 编码读取容器内文件（避免二进制传输问题）
3. 解码并写入宿主机 download 目录
4. 返回 HTTP 下载链接

支持的文件类型：
- 图表 PNG/JPG（generate_chart 生成）
- 分析报告 MD/HTML
- 数据文件 CSV/JSON
- 任意沙箱内生成的文件
"""
import base64
from pathlib import Path
from urllib.parse import quote

from langchain_core.tools import tool

from ..config import DOWNLOAD_DIR, SANDBOX_ALLOW_LOCAL_FALLBACK, SANDBOX_WORK_DIR
from ..log_utils import agent_logger
from .path_safety import UnsafePathError, resolve_under

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


def _is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_EXTS


def _build_result_message(
    filename: str,
    download_url: str,
    size_kb: float,
    *,
    sandbox: bool,
    remote_path: str | None = None,
    local_path=None,
) -> str:
    """构造返回给模型的文案。

    图片直接给出可内嵌的 Markdown（``![...](url)``），并明确要求模型面向用户
    时内嵌显示、不要暴露沙箱 / 本地文件系统路径（用户点不开也无意义）。
    非图片文件才退回普通下载链接。
    """
    lines = []
    if _is_image(filename):
        lines.append("✅ 图片已导出，可直接在回复中内嵌显示：")
        lines.append(f"![{filename}]({download_url})")
        lines.append(
            "\n请在面向用户的回复中直接使用上面的 Markdown 把图片显示出来；"
            "不要把沙箱路径或本地文件系统路径写给用户。"
        )
    else:
        lines.append(f"✅ 文件已导出，下载链接：[下载 {filename}]({download_url})")
    lines.append(f"文件大小: {size_kb:.1f} KB")
    if remote_path is not None:
        # 仅供调试/定位，明确标注不要展示给用户
        lines.append(f"(沙箱内部路径 {remote_path}，仅供工具定位，不要展示给用户)")
    if local_path is not None:
        lines.append(f"(本地路径 {local_path}，不要展示给用户)")
    if not sandbox:
        lines.append("(注: 沙箱不可用，使用本地文件)")
    return "\n".join(lines)


def _get_docker_container(remote_path: str = ""):
    """定位当前 run 绑定的 Docker 沙箱容器。

    只允许访问 ``sandbox_holder`` 中当前请求绑定的容器；不再扫描所有
    ``erp-sandbox-*`` 容器做兜底——那会把 A 用户的文件泄露给 B 用户。
    """
    try:
        import docker
        client = docker.from_env()
    except Exception as e:
        agent_logger.warning(f"Cannot connect to Docker: {e}")
        return None, None

    try:
        from ..backends.sandbox_holder import get_sandbox

        bound = get_sandbox()
        bound_name = getattr(bound, "container_name", None) if bound else None
        if not bound_name:
            client.close()
            return None, None

        try:
            container = client.containers.get(bound_name)
            if container.status == "running":
                return container, client
        except Exception:
            agent_logger.warning(f"Bound sandbox '{bound_name}' unavailable")

        client.close()
        return None, None
    except Exception as e:
        agent_logger.warning(f"Cannot connect to Docker: {e}")
        try:
            client.close()
        except Exception:
            pass
        return None, None


def _resolve_local_source(remote_path: str) -> Path:
    """把本地回退路径限制在 ``DOWNLOAD_DIR`` 内。

    相对路径按 ``DOWNLOAD_DIR`` 解析；绝对路径同样必须落在
    ``DOWNLOAD_DIR`` 内（``resolve()`` 展开符号链接后再比较），否则拒绝。
    这样即使本地回退被显式打开，也不会读取宿主机任意文件。
    """
    base = DOWNLOAD_DIR.resolve()
    if not remote_path or "\x00" in remote_path:
        raise UnsafePathError("路径不能为空或包含 NUL 字符")
    raw = Path(remote_path)
    target = (raw if raw.is_absolute() else base / raw).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise UnsafePathError(f"路径不在下载目录内: {remote_path}") from exc
    return target


@tool
def download_sandbox_file(remote_path: str, filename: str = "") -> str:
    """从沙箱容器中下载文件到宿主机，生成 HTTP 下载链接。

    工作原理：
    - 通过 Docker SDK 连接到沙箱容器
    - 读取容器内文件（支持文本和二进制）
    - 保存到宿主机 download 目录
    - 返回 HTTP 下载链接

    Args:
        remote_path: 沙箱内的文件路径（如 /mnt/user-data/outputs/report.md）
        filename: 下载后的文件名（默认使用原文件名）

    Returns:
        下载链接和本地路径，或错误信息
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if not filename:
        filename = Path(remote_path).name

    # 文件名来自模型/请求，必须先锁定在 DOWNLOAD_DIR 内，后续写入复用该路径。
    try:
        target = resolve_under(DOWNLOAD_DIR, filename)
    except UnsafePathError as exc:
        return f"文件名非法: {exc}"
    filename = target.name

    # === 方式1：从 Docker 沙箱下载 ===
    container, docker_client = _get_docker_container(remote_path)
    if container is not None:
        try:
            # 检查文件是否存在。
            # 必须用 exec 列表形式：exec_run 默认 shell=False，走 execve 而非 shell，
            # 写成 "test -f x && echo EXISTS" 会把 && 当参数传给 test，
            # 返回 exit 2（"extra argument"），被误判成文件不存在。
            check = container.exec_run(["test", "-f", remote_path])
            if check.exit_code != 0:
                docker_client.close()
                return f"文件不存在于沙箱中: {remote_path}"

            # 获取文件大小
            size_check = container.exec_run(["stat", "-c", "%s", remote_path])
            file_size = int(size_check.output.decode().strip()) if size_check.exit_code == 0 else 0

            # 使用 base64 读取文件（支持二进制）
            read_result = container.exec_run(
                ["base64", remote_path],
                demux=True,
            )

            if read_result.exit_code != 0:
                docker_client.close()
                return f"无法读取沙箱文件: {remote_path}"

            stdout = read_result.output[0] if isinstance(read_result.output, tuple) else read_result.output
            if not stdout:
                docker_client.close()
                return f"文件内容为空: {remote_path}"

            # 解码 base64
            content = base64.b64decode(stdout.strip())

            # 写入下载目录（target 已在 DOWNLOAD_DIR 内解析并校验）
            target.write_bytes(content)

            docker_client.close()

            # 文件名可能含中文/空格，URL 里必须做百分号编码，否则浏览器无法直接打开
            download_url = f"http://localhost:8000/api/download/{quote(filename)}"
            agent_logger.info(
                f"File downloaded from sandbox: {remote_path} -> {target} ({len(content)} bytes)"
            )
            return _build_result_message(
                filename,
                download_url,
                file_size / 1024,
                sandbox=True,
                remote_path=remote_path,
                local_path=target,
            )

        except Exception as e:
            if docker_client:
                docker_client.close()
            agent_logger.error(f"Sandbox download error: {e}")
            # 回退到本地文件检查

    # === 方式2：回退到本地文件（仅本地开发显式开启）===
    # 默认关闭：沙箱不可用时不得把宿主机文件系统暴露给模型。
    if SANDBOX_ALLOW_LOCAL_FALLBACK:
        try:
            source = _resolve_local_source(remote_path)
        except UnsafePathError as exc:
            return f"路径非法: {exc}"

        if source.exists():
            if source.is_dir():
                return f"路径是目录而非文件: {remote_path}"

            import shutil
            shutil.copy2(source, target)

            download_url = f"http://localhost:8000/api/download/{quote(filename)}"
            agent_logger.info(f"File downloaded (local fallback): {source} -> {target}")
            return _build_result_message(
                filename,
                download_url,
                source.stat().st_size / 1024,
                sandbox=False,
                remote_path=remote_path,
                local_path=target,
            )

        return (
            f"文件下载失败:\n"
            f"- 沙箱路径: {remote_path}\n"
            f"- 沙箱状态: {'运行中' if container else '不可用'}\n"
            f"- 本地文件: 不存在\n"
            f"请确认文件已在沙箱中生成。"
        )

    return (
        f"文件下载失败:\n"
        f"- 沙箱路径: {remote_path}\n"
        f"- 沙箱状态: {'运行中' if container else '不可用'}\n"
        f"- 本地回退: 未启用（SANDBOX_ALLOW_LOCAL_FALLBACK=false）\n"
        f"请确认文件已在沙箱中生成。"
    )


@tool
def list_sandbox_files(path: str = SANDBOX_WORK_DIR) -> str:
    """列出沙箱内指定目录的文件。

    Args:
        path: 沙箱内的目录路径（默认 /mnt/user-data/workspace）

    Returns:
        文件列表
    """
    container, docker_client = _get_docker_container(path)
    if container is None:
        return "沙箱不可用"

    try:
        result = container.exec_run(["ls", "-lah", path])
        output = result.output.decode("utf-8", errors="replace") if result.output else ""
        if docker_client:
            docker_client.close()
        return f"沙箱目录 {path}:\n{output}" if output else f"目录为空: {path}"
    except Exception as e:
        if docker_client:
            docker_client.close()
        return f"列出目录失败: {e}"

"""
沙箱文件下载工具（Harness — 真实 Docker 文件提取）
从沙箱容器中下载文件到宿主机 src/download/ 目录，供用户通过 HTTP 访问。

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
import os
import base64
from pathlib import Path
from urllib.parse import quote
from langchain_core.tools import tool

from ..log_utils import agent_logger

DOWNLOAD_DIR = Path(__file__).parent.parent.parent / "download"


def _path_has_content(container, path: str) -> bool:
    """判断路径是否"存在且有内容"。

    仅用 ``test -e`` 不足以在多个沙箱间消歧：``/workspace/output`` 这类目录
    在所有沙箱里都由 bootstrap 创建，目录本身存在但可能是空的。
    因此目录还要额外确认非空。

    注意 exec_run 默认 ``shell=False``，涉及管道/重定向必须显式走 ``sh -c``。
    """
    import shlex

    if container.exec_run(["test", "-e", path]).exit_code != 0:
        return False

    if container.exec_run(["test", "-d", path]).exit_code == 0:
        probe = container.exec_run(
            ["sh", "-c", f"ls -A {shlex.quote(path)} 2>/dev/null | head -n 1"]
        )
        return bool((probe.output or b"").strip())

    return True


def _get_docker_container(remote_path: str = ""):
    """定位 Docker 沙箱容器。

    预热池会同时存在多个 ``erp-sandbox-*`` 容器：一个被当前会话 claim，
    其余处于 WARM 空闲待命。早期实现直接取 ``containers.list()`` 的第一个，
    可能拿到空容器，导致明明生成成功的文件被报告成"文件不存在"。

    这里按三级优先级定位：
    1. 当前请求上下文绑定的沙箱（``sandbox_holder``）—— 最准确，不受容器数量影响
    2. 固定命名容器（单容器部署场景）
    3. 在候选容器中按 ``remote_path`` 是否存在消歧
    4. 兜底取第一个运行中的容器

    Args:
        remote_path: 待访问的沙箱内路径，用于第 3 级消歧
    """
    try:
        import docker
        client = docker.from_env()
    except Exception as e:
        agent_logger.warning(f"Cannot connect to Docker: {e}")
        return None, None

    try:
        # 1) 当前请求绑定的沙箱
        try:
            from ..backends.sandbox_holder import get_sandbox

            bound = get_sandbox()
            bound_name = getattr(bound, "container_name", None) if bound else None
            if bound_name:
                try:
                    container = client.containers.get(bound_name)
                    if container.status == "running":
                        return container, client
                except Exception:
                    agent_logger.debug(
                        f"Bound sandbox '{bound_name}' unavailable, falling back"
                    )
        except Exception:
            pass

        # 2) 固定命名容器
        for name in ("erp-sandbox", "erp-sandbox-default_user"):
            try:
                container = client.containers.get(name)
                if container.status == "running":
                    return container, client
            except Exception:
                continue

        candidates = [
            c
            for c in client.containers.list()
            if c.name.startswith("erp-sandbox-") and c.status == "running"
        ]

        # 3) 按目标路径存在性消歧
        if remote_path and len(candidates) > 1:
            for container in candidates:
                try:
                    if _path_has_content(container, remote_path):
                        return container, client
                except Exception:
                    continue

        if candidates:
            return candidates[0], client

        client.close()
        return None, None
    except Exception as e:
        agent_logger.warning(f"Cannot connect to Docker: {e}")
        try:
            client.close()
        except Exception:
            pass
        return None, None


@tool
def download_sandbox_file(remote_path: str, filename: str = "") -> str:
    """从沙箱容器中下载文件到宿主机，生成 HTTP 下载链接。

    工作原理：
    - 通过 Docker SDK 连接到沙箱容器
    - 读取容器内文件（支持文本和二进制）
    - 保存到宿主机 download 目录
    - 返回 HTTP 下载链接

    Args:
        remote_path: 沙箱内的文件路径（如 /workspace/report.md, /tmp/chart.png）
        filename: 下载后的文件名（默认使用原文件名）

    Returns:
        下载链接和本地路径，或错误信息
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if not filename:
        filename = Path(remote_path).name

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

            # 写入下载目录
            target = DOWNLOAD_DIR / filename
            target.write_bytes(content)

            docker_client.close()

            # 文件名可能含中文/空格，URL 里必须做百分号编码，否则浏览器无法直接打开
            download_url = f"http://localhost:8000/api/download/{quote(filename)}"
            agent_logger.info(
                f"File downloaded from sandbox: {remote_path} -> {target} ({len(content)} bytes)"
            )
            return (
                f"✅ 文件已从沙箱下载!\n"
                f"沙箱路径: {remote_path}\n"
                f"文件大小: {file_size / 1024:.1f} KB\n"
                f"下载链接: {download_url}\n"
                f"本地路径: {target}"
            )

        except Exception as e:
            if docker_client:
                docker_client.close()
            agent_logger.error(f"Sandbox download error: {e}")
            # 回退到本地文件检查

    # === 方式2：回退到本地文件（开发模式 / 沙箱不可用）===
    source = Path(remote_path)
    if source.exists():
        if source.is_dir():
            return f"路径是目录而非文件: {remote_path}"

        target = DOWNLOAD_DIR / filename
        import shutil
        shutil.copy2(source, target)

        download_url = f"http://localhost:8000/api/download/{quote(filename)}"
        agent_logger.info(f"File downloaded (local fallback): {source} -> {target}")
        return (
            f"✅ 文件已下载!\n"
            f"路径: {remote_path}\n"
            f"文件大小: {source.stat().st_size / 1024:.1f} KB\n"
            f"下载链接: {download_url}\n"
            f"本地路径: {target}\n"
            f"(注: 沙箱不可用，使用本地文件)"
        )

    return (
        f"文件下载失败:\n"
        f"- 沙箱路径: {remote_path}\n"
        f"- 沙箱状态: {'运行中' if container else '不可用'}\n"
        f"- 本地文件: {'不存在' if not source.exists() else '存在'}\n"
        f"请确认文件已在沙箱中生成。"
    )


@tool
def list_sandbox_files(path: str = "/workspace") -> str:
    """列出沙箱内指定目录的文件。

    Args:
        path: 沙箱内的目录路径（默认 /workspace）

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

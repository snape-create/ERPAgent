"""
Docker 沙箱后端
继承 deepagents BaseSandbox，通过 Docker SDK 在隔离容器中执行命令和操作文件。
容器名: erp-sandbox（由 SandboxManager 管理）.
"""
import docker
import base64
import io
import os
import shlex
import tarfile
import uuid
from typing import Optional

from deepagents.backends.sandbox import (
    BaseSandbox, ExecuteResponse,
    FileDownloadResponse, FileUploadResponse,
)
from deepagents.backends import DEFAULT_EXECUTE_TIMEOUT
from ..log_utils import sandbox_logger
from ..config import SANDBOX_WORK_DIR


# 单次 docker exec 参数长度上限约 128KB（MAX_ARG_STRLEN）。
# 超过该阈值的内容改走分块写入，避免 "Argument list too long"。
_INLINE_WRITE_LIMIT = 60_000


class CustomOpenSandbox(BaseSandbox):
    """
    Docker 容器沙箱后端

    通过 Docker SDK exec_run 在已运行的容器中执行命令。
    继承 BaseSandbox 后，ls/read/write/edit/glob/grep 等文件操作
    自动委托给 execute()（即 docker exec）。

    使用方式：
        backend = DockerSandboxBackend(container_name="erp-sandbox")
        result = backend.execute("python -c 'print(1+1)'")
    """

    def __init__(
        self,
        container_name: str = "erp-sandbox",
        work_dir: str = SANDBOX_WORK_DIR,
        timeout: int = DEFAULT_EXECUTE_TIMEOUT,
    ):
        self._container_name = container_name
        self._work_dir = work_dir
        self._default_timeout = timeout
        self._client: Optional[docker.DockerClient] = None
        self._container = None
        self._connect()

    def _connect(self):
        """连接到 Docker 容器"""
        try:
            self._client = docker.from_env()
            self._container = self._client.containers.get(self._container_name)
            if self._container.status != "running":
                raise RuntimeError(
                    f"Container '{self._container_name}' is not running "
                    f"(status: {self._container.status})"
                )
            # 确保工作目录存在
            self._container.exec_run(f"mkdir -p {self._work_dir}")
            sandbox_logger.info(
                f"Docker sandbox connected: {self._container_name} "
                f"({self._container.id[:12]})"
            )
        except docker.errors.NotFound:
            raise RuntimeError(
                f"Docker container '{self._container_name}' not found. "
                f"Please start it with:\n"
                f"  docker run -d --name {self._container_name} "
                f"-w {self._work_dir} python:3.11-slim sleep infinity"
            )
        except Exception as e:
            raise RuntimeError(f"Failed to connect to Docker sandbox: {e}")

    @property
    def id(self) -> str:
        """沙箱唯一标识"""
        if self._container:
            return self._container.id[:12]
        return "disconnected"

    @property
    def container_id(self) -> str:
        """容器完整ID（供 SandboxManager 使用）"""
        if self._container:
            return self._container.id
        return ""

    @property
    def container_name(self) -> str:
        """容器名称"""
        return self._container_name

    # ============================================================
    # 核心执行
    # ============================================================

    def execute(self,command: str,*,timeout: int | None = None,) -> ExecuteResponse:
        """
        在 Docker 容器中执行 shell 命令

        Args:
            command: shell 命令字符串
            timeout: 超时秒数（None 使用默认值）

        Returns:
            ExecuteResponse(output, exit_code, truncated)
        """
        if self._container is None:
            return ExecuteResponse(
                output="[沙箱未连接] 请先启动 Docker 容器",
                exit_code=-1,
            )

        try:
            # 超时控制：coreutils timeout 先发 TERM，5 秒后 KILL（超时退出码 124）。
            # workdir 已由 exec_run 生效，无需再拼 "cd xxx &&"。
            effective_timeout = (
                timeout if timeout is not None else self._default_timeout
            )
            if effective_timeout and effective_timeout > 0:
                cmd = [
                    "timeout", "-k", "5", str(int(effective_timeout)),
                    "bash", "-c", command,]
            else:
                cmd = ["bash", "-c", command]

            exec_result = self._container.exec_run(
                cmd=cmd,
                demux=True,  # 分离 stdout/stderr
                workdir=self._work_dir,
            )

            exit_code = exec_result.exit_code
            stdout, stderr = exec_result.output

            # 合并输出
            output_parts = []
            if stdout:
                output_parts.append(
                    stdout.decode("utf-8", errors="replace")
                )
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(stderr_text)

            output = "\n".join(output_parts) if output_parts else ""


            truncated = False
            max_bytes = 5_000_000
            if len(output) > max_bytes:
                output = output[:max_bytes]
                truncated = True # 错误和成功信息分开输出
                sandbox_logger.warning(
                    f"Sandbox output truncated at {max_bytes} chars "
                    f"(command: {command[:80]!r})"
                )

            return ExecuteResponse(
                output=output,
                exit_code=exit_code,
                truncated=truncated,
            )

        except Exception as e:
            sandbox_logger.error(f"Docker exec failed: {e}")
            return ExecuteResponse(
                output=f"[执行错误] {str(e)}",
                exit_code=-1,
            )

    # ============================================================
    # 文件操作 — 真实实现（通过 docker exec / tar 流）
    # ============================================================

    def read_file(self, path: str) -> str:
        """读取沙箱内文件内容（文本）"""
        resp = self.execute(f"cat '{path}'")
        if resp.exit_code != 0:
            raise FileNotFoundError(f"Cannot read file: {path} — {resp.output}")
        return resp.output

    def read_file_bytes(self, path: str) -> bytes:
        """读取沙箱内文件内容（二进制，base64 传输）"""
        resp = self.execute(f"base64 {shlex.quote(path)}")
        if resp.exit_code != 0:
            raise FileNotFoundError(f"Cannot read file: {path} — {resp.output}")
        if resp.truncated:
            # base64 被截断后可能解出半截数据而不报错，必须显式失败
            raise IOError(
                f"File too large for single-shot transfer: {path}. "
                f"Use download_files in chunks or raise max_bytes."
            )
        return base64.b64decode(resp.output.strip())

    def write_file(self, path: str, content: str | bytes) -> str:
        """写入内容到沙箱内文件（自动创建父目录）"""
        data = content.encode("utf-8") if isinstance(content, str) else content

        dir_path = os.path.dirname(path) or "/"
        self.execute(f"mkdir -p {shlex.quote(dir_path)}")

        if len(data) <= _INLINE_WRITE_LIMIT:
            # 小文件：单条命令写入（base64 避免 shell 转义问题）
            encoded = base64.b64encode(data).decode("ascii")
            resp = self.execute(
                f"printf '%s' {shlex.quote(encoded)} | base64 -d > {shlex.quote(path)}"
            )
            if resp.exit_code != 0:
                raise IOError(f"Cannot write file: {path} — {resp.output}")
        else:
            # 大文件：复用分块写入，避免撞单参数 128KB 上限
            try:
                self._write_bytes_via_exec(path, data)
            except Exception as e:
                raise IOError(f"Cannot write file: {path} — {e}") from e

        return f"OK: {path}"

    def list_dir(self, path: str = ".") -> list[str]:
        """列出沙箱内目录内容"""
        resp = self.execute(f"ls -1 '{path}'")
        if resp.exit_code != 0:
            raise FileNotFoundError(f"Cannot list dir: {path} — {resp.output}")
        items = [line.strip() for line in resp.output.strip().split("\n") if line.strip()]
        return items

    def file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        resp = self.execute(f"test -e '{path}' && echo YES || echo NO")
        return "YES" in resp.output

    def is_directory(self, path: str) -> bool:
        """检查路径是否为目录"""
        resp = self.execute(f"test -d '{path}' && echo YES || echo NO")
        return "YES" in resp.output

    def edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """编辑文件：替换文本块"""
        content = self.read_file(path)
        if old_text not in content:
            return f"Error: old_text not found in {path}"
        content = content.replace(old_text, new_text, 1)
        return self.write_file(path, content)

    def glob(self, pattern: str, base_path: str = ".") -> list[str]:
        """文件模式匹配（支持递归）"""
        resp = self.execute(f"find '{base_path}' -path '{pattern}' -type f 2>/dev/null | head -200")
        if resp.exit_code != 0:
            return []
        return [line.strip() for line in resp.output.strip().split("\n") if line.strip()]

    def grep(self, pattern: str, path: str = ".", recursive: bool = True) -> list[str]:
        """在文件中搜索文本模式"""
        flag = "-rn" if recursive else "-n"
        resp = self.execute(f"grep {flag} '{pattern}' '{path}' 2>/dev/null | head -100")
        if resp.exit_code != 0:
            return []
        return [line.strip() for line in resp.output.strip().split("\n") if line.strip()]

    def mkdir(self, path: str) -> str:
        """创建目录（含父目录）"""
        resp = self.execute(f"mkdir -p '{path}'")
        if resp.exit_code != 0:
            raise IOError(f"Cannot mkdir: {path} — {resp.output}")
        return f"OK: {path}"

    def rm(self, path: str) -> str:
        """删除文件或目录"""
        resp = self.execute(f"rm -rf '{path}'")
        if resp.exit_code != 0:
            raise IOError(f"Cannot rm: {path} — {resp.output}")
        return f"OK: removed {path}"

    def cp(self, src: str, dst: str) -> str:
        """复制文件或目录"""
        resp = self.execute(f"cp -r '{src}' '{dst}'")
        if resp.exit_code != 0:
            raise IOError(f"Cannot cp: {src} -> {dst} — {resp.output}")
        return f"OK: {src} -> {dst}"

    def mv(self, src: str, dst: str) -> str:
        """移动文件或目录"""
        resp = self.execute(f"mv '{src}' '{dst}'")
        if resp.exit_code != 0:
            raise IOError(f"Cannot mv: {src} -> {dst} — {resp.output}")
        return f"OK: {src} -> {dst}"

    def cat(self, path: str) -> str:
        """读取文件内容（同 read_file）"""
        return self.read_file(path)

    def pwd(self) -> str:
        """获取当前工作目录"""
        resp = self.execute("pwd")
        return resp.output.strip()

    def env(self) -> str:
        """获取沙箱环境变量"""
        resp = self.execute("env")
        return resp.output

    def pip_install(self, package: str) -> str:
        """安装 Python 包"""
        resp = self.execute(f"pip install {package} -q", timeout=120)
        if resp.exit_code != 0:
            return f"pip install failed: {resp.output}"
        return f"OK: installed {package}"

    def python_exec(self, script: str) -> ExecuteResponse:
        """执行 Python 脚本（写入临时文件再运行）"""
        self.write_file("/tmp/_sandbox_script.py", script)
        return self.execute("python /tmp/_sandbox_script.py", timeout=60)

    def go_exec(self, code: str) -> ExecuteResponse:
        """执行 Go 代码"""
        self.write_file("/tmp/_sandbox_main.go", code)
        return self.execute("cd /tmp && go run _sandbox_main.go", timeout=60)

    def node_exec(self, code: str) -> ExecuteResponse:
        """执行 Node.js 代码"""
        self.write_file("/tmp/_sandbox_script.js", code)
        return self.execute("node /tmp/_sandbox_script.js", timeout=60)

    # ============================================================
    # 生命周期
    # ============================================================

    def ping(self) -> bool:
        """健康检查：容器是否仍在运行"""
        try:
            if self._container is None:
                return False
            self._container.reload()
            return self._container.status == "running"
        except Exception:
            return False

    def destroy(self):
        """断开连接（不销毁容器，容器由 SandboxManager 管理）"""
        self._container = None
        if self._client:
            self._client.close()
            self._client = None
        sandbox_logger.info("Docker sandbox disconnected")

    def destroy_container(self):
        """强制停止并删除容器（由 SandboxManager 调用）"""
        if self._container:
            try:
                self._container.stop(timeout=5)
                self._container.remove(force=True)
                sandbox_logger.info(f"Container destroyed: {self._container_name}")
            except Exception as e:
                sandbox_logger.warning(f"Error destroying container: {e}")
            finally:
                self._container = None
        if self._client:
            self._client.close()
            self._client = None

    # ============================================================
    # 文件上传/下载（tar 流方式，高效可靠）
    # ============================================================

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从容器中下载文件（base64 传输）"""
        results = []
        for path in paths:
            try:
                resp = self.execute(f"base64 {shlex.quote(path)}")
                if resp.truncated:
                    # 截断的 base64 可能解码成半截数据，必须显式报错
                    results.append(FileDownloadResponse(
                        path=path, content=None, error="file_too_large",
                    ))
                elif resp.exit_code == 0 and resp.output.strip():
                    content = base64.b64decode(resp.output.strip())
                    results.append(FileDownloadResponse(path=path, content=content, error=None))
                else:
                    results.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
            except Exception as e:
                results.append(FileDownloadResponse(path=path, content=None, error=str(e)))
        return results

    def download_file_to_host(self, remote_path: str, local_path: str) -> str:
        """从沙箱下载文件到宿主机指定路径"""
        try:
            content = self.read_file_bytes(remote_path)
            from pathlib import Path
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_bytes(content)
            return f"OK: {remote_path} -> {local_path}"
        except Exception as e:
            return f"Download failed: {e}"

    def _write_bytes_via_exec(self, path: str, content: bytes) -> None:
        """用 exec + base64 分块写入文件。

        容器以 ``read_only`` 启动时，Docker daemon 会拒绝所有 ``put_archive``
        请求（"container rootfs is marked read-only"），即使目标是 /workspace
        这类 tmpfs 挂载点也一样 —— 这是 daemon 层的全局检查，不看具体路径。
        而 ``docker exec`` 不受此限制，tmpfs 挂载点本身可写。

        命令行参数上限约 128KB，这里按 60KB 分块，单块只含 base64 字符
        （A-Za-z0-9+/=），无需额外转义。
        """
        b64 = base64.b64encode(content).decode("ascii")
        tmp = f"/tmp/.upload-{uuid.uuid4().hex[:8]}.b64"
        chunk_size = 60000

        try:
            resp = self.execute(f"rm -f {tmp}")
            if resp.exit_code != 0:
                raise RuntimeError(f"清理临时文件失败: {resp.output[:200]}")

            for start in range(0, len(b64), chunk_size):
                part = b64[start:start + chunk_size]
                resp = self.execute(f"printf '%s' '{part}' >> {tmp}")
                if resp.exit_code != 0:
                    raise RuntimeError(
                        f"分块写入失败（offset={start}）: {resp.output[:200]}"
                    )

            dir_name = os.path.dirname(path) or "/"
            resp = self.execute(
                f"mkdir -p '{dir_name}' && "
                f"base64 -d {tmp} > '{path}' && rm -f {tmp}"
            )
            if resp.exit_code != 0:
                raise RuntimeError(f"base64 解码落盘失败: {resp.output[:200]}")
        except Exception:
            self.execute(f"rm -f {tmp}")
            raise

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到容器。

        优先用 Docker 原生 tar 流（快）；容器只读时回退到 exec + base64。
        """
        results = []
        for path, content in files:
            try:
                dir_name = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
                file_name = path.split("/")[-1]

                tar_stream = io.BytesIO()
                with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                    info = tarfile.TarInfo(name=file_name)
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
                tar_stream.seek(0)

                try:
                    self._container.put_archive(dir_name, tar_stream.read())
                except Exception as docker_err:
                    sandbox_logger.debug(
                        f"put_archive rejected ({docker_err}), "
                        f"falling back to exec+base64 for {path}"
                    )
                    self._write_bytes_via_exec(path, content)

                results.append(FileUploadResponse(path=path, error=None))
            except Exception as e:
                results.append(FileUploadResponse(path=path, error=str(e)))
        return results

    def upload_directory(self, local_dir: str, remote_dir: str) -> str:
        """上传整个目录到沙箱（tar 打包传输）"""
        from pathlib import Path
        local_path = Path(local_dir)
        if not local_path.exists():
            return f"Error: local directory not found: {local_dir}"

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w:gz") as tar:
            tar.add(str(local_path), arcname=".")
        tar_stream.seek(0)

        try:
            self.execute(f"mkdir -p '{remote_dir}'")
            self._container.put_archive(remote_dir, tar_stream.read())
            return f"OK: uploaded {local_dir} -> {remote_dir}"
        except Exception as e:
            return f"Upload directory failed: {e}"


# 向后兼容别名
DockerSandboxBackend = CustomOpenSandbox

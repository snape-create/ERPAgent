"""
可热替换的沙箱代理。

DeepAgents 会在 Agent 创建时持有 Backend 对象。沙箱故障后如果直接把
CustomOpenSandbox 换成新实例，CompositeBackend、文件工具和自定义工具都会
继续引用旧对象。该 Proxy 自身是一个 BaseSandbox，因此对外句柄稳定，内部
可以原子地切换到底层新容器。
"""
from __future__ import annotations

from threading import RLock
from typing import Any

from deepagents.backends.protocol import (
    GrepResult,
    LsResult,
    ReadResult,
)
from deepagents.backends.sandbox import (
    BaseSandbox,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    # 复用基类的命令构建与解析逻辑，只额外增加截断检查。
    # 注意：以下均为 deepagents 内部函数，升级依赖时需要复核签名。
    _build_grep_cmd,
    _build_ls_cmd,
    _build_read_cmd,
    _parse_grep_output,
    _parse_ls_output,
    _parse_read_output,
)

from ..log_utils import sandbox_logger

# 单次返回给模型的结果规模上限：超出即裁剪，避免静默撑爆上下文。
MAX_LS_ENTRIES = 2000
MAX_GREP_MATCHES = 500


class SandboxBackendProxy(BaseSandbox):
    """为 DeepAgents 提供稳定的、可热替换的 ``BaseSandbox`` 句柄。"""

    def __init__(self, backend: BaseSandbox | None = None):
        self._backend = backend
        self._lock = RLock()
        self._generation = 0

    @property
    def backend(self) -> BaseSandbox:
        """获取当前后端快照；调用方不应缓存返回值。"""
        with self._lock:
            if self._backend is None:
                raise RuntimeError("No sandbox backend available")
            return self._backend

    @property
    def generation(self) -> int:
        """当前热替换代次，便于日志与测试判断。"""
        with self._lock:
            return self._generation

    @property
    def id(self) -> str:
        return self.backend.id

    @property
    def container_name(self) -> str:
        return str(getattr(self.backend, "container_name", ""))

    @property
    def container_id(self) -> str:
        return str(getattr(self.backend, "container_id", ""))

    def replace_backend(self, new_backend: BaseSandbox) -> None:
        """原子替换底层容器连接，保持 Proxy 对象本身不变。"""
        if new_backend is None:
            raise ValueError("new_backend must not be None")

        with self._lock:
            old_backend = self._backend
            self._backend = new_backend
            self._generation += 1

        # Manager 重建时已经负责删除旧容器；这里仅尽力关闭旧 SDK 连接。
        if old_backend is not None and old_backend is not new_backend:
            try:
                destroy = getattr(old_backend, "destroy", None)
                if callable(destroy):
                    destroy()
            except Exception as exc:  # 不应阻断新容器接管
                sandbox_logger.warning(f"Failed to disconnect old sandbox backend: {exc}")

        sandbox_logger.info(
            "Sandbox backend hot-swapped "
            f"(generation={self.generation}, id={getattr(new_backend, 'id', 'unknown')})"
        )

    # BaseSandbox 的三个抽象能力。其余标准文件操作由 BaseSandbox 基于这些能力实现。
    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        return self.backend.execute(command, timeout=timeout)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.backend.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.backend.download_files(paths)

    # ============================================================
    # 协议方法的截断保护
    #
    # BaseSandbox 的 read/ls/grep 把结构化结果序列化成 JSON 通过 execute()
    # 的 stdout 传回。底层 execute 一旦因超过传输上限而截断，报文就被破坏：
    #   - read:  json.loads 失败 → 报 "unexpected server response"（误导）
    #   - ls/grep: 逐行解析会直接跳过残缺行 → 静默丢数据
    # 这里在解析前先检查 truncated，把提示放进 error 字段（错误通道），
    # 而不是往 output（数据通道）里拼接文字，避免二次破坏报文。
    # ============================================================

    def read(self,file_path: str,offset: int = 0,limit: int = 2000,) -> ReadResult:
        response = self.execute(_build_read_cmd(file_path, offset, limit))
        if response.truncated:
            return ReadResult(
                error=(
                    f"File '{file_path}': 单页内容超过沙箱单次传输上限，结果已被截断。"
                    f"请减小 limit（当前 {limit}）并从 offset={offset} 重新分页读取。"
                )
            )
        return _parse_read_output(response.output, file_path)

    def ls(self, path: str) -> LsResult:
        # 多取一条用于探测"是否还有更多"，避免在源头全量输出。
        response = self.execute(f"( {_build_ls_cmd(path)} ) | head -n {MAX_LS_ENTRIES + 1}")
        if response.truncated: 
            return LsResult(
                error=(
                    f"Path '{path}': 目录条目过多，输出被截断，结果不完整。"
                    f"请改用 glob(pattern, path) 缩小匹配范围。"
                )
            )
        result = _parse_ls_output(response.output, path)
        if result.entries is not None and len(result.entries) > MAX_LS_ENTRIES:
            return LsResult(
                entries=result.entries[:MAX_LS_ENTRIES],
                error=(
                    f"Path '{path}': 目录条目超过 {MAX_LS_ENTRIES} 项，"
                    f"仅返回前 {MAX_LS_ENTRIES} 项。请改用 glob(pattern, path) 缩小范围。"
                ),
            )
        return result

    def grep(self,pattern: str,path: str | None = None,glob: str | None = None,*,max_count: int | None = None,) -> GrepResult:
        # 调用方不指定上限时给默认值，指定了也不允许超过硬顶。
        effective_max = (MAX_GREP_MATCHES if max_count is None else min(max_count, MAX_GREP_MATCHES))
        response = self.execute(
            _build_grep_cmd(
                pattern,
                path,
                glob,
                effective_max,
                exclude_dirs=self.grep_exclude_dirs,
            )
        )
        if response.truncated:
            return GrepResult(
                error=(
                    f"Path '{path or '.'}': 搜索输出被截断，匹配结果不完整。"
                    f"请使用更精确的 pattern、glob 或更小的 max_count。"
                )
            )
        return _parse_grep_output(response, path, effective_max)

    # glob 无需覆写：_parse_glob_output 本身就会读取 response.truncated，
    # 丢弃被截断的末行并通过 GlobResult.truncated 告知调用方。

    # 下面是项目已有工具/中间件使用的扩展接口，继续透明委托给真实实现。
    def ping(self) -> bool:
        try:
            ping = getattr(self.backend, "ping", None)
            if callable(ping):
                return bool(ping())
            return self.execute("echo ok", timeout=5).exit_code == 0
        except Exception:
            return False

    def destroy(self) -> None:
        """只断开当前连接；容器销毁由 SandboxManager 负责。"""
        try:
            destroy = getattr(self.backend, "destroy", None)
            if callable(destroy):
                destroy()
        except RuntimeError:
            return

    def __getattr__(self, name: str) -> Any:
        """兼容项目的 ``write_file``、``file_exists``、``cp`` 等扩展方法。"""
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.backend, name)

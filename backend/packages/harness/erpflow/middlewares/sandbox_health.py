"""沙箱健康检查与透明恢复中间件。"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState, Runtime

from ..log_utils import middleware_logger

if TYPE_CHECKING:
    from deepagents.backends.sandbox import BaseSandbox

    from ..backends.sandbox_manager import SandboxManager
    from ..backends.sandbox_proxy import SandboxBackendProxy


class SandboxHealthMiddleware(AgentMiddleware[AgentState[Any], None, Any]):
    """在每次 Agent 运行前验证并在需要时重建用户沙箱。

    ``CompositeBackend``、文件工具和自定义工具都持有同一个
    :class:`SandboxBackendProxy`。故障时这里只替换 Proxy 的底层后端，再执行
    bootstrap 回调，因此无需重新创建 Agent 或丢失会话图状态。
    """

    def __init__(
        self,
        sandbox_manager: SandboxManager | None = None,
        user_id: str = "default_user",
        sandbox_backend: SandboxBackendProxy | None = None,
        on_rebuild: Callable[[BaseSandbox], None] | None = None,
        check_interval: float = 30.0,
        failure_threshold: int = 1,
    ) -> None:
        self._sandbox_manager = sandbox_manager
        self._user_id = user_id
        self._sandbox_backend = sandbox_backend
        self._on_rebuild = on_rebuild
        self._check_interval = check_interval
        self._failure_threshold = max(1, failure_threshold)
        self._last_check: float = 0.0
        self._healthy = True
        self._consecutive_failures = 0
        # 同一 Graph 可能被多个并发 run 复用；bool 会让第二个 run 不再
        # acquire，第一个 run 的 after_agent 却把唯一的 lease 释放掉。
        self._lease_depth = 0
        self._lease_lock = threading.Lock()
        self.tools = []

    @property
    def name(self) -> str:
        return "SandboxHealthMiddleware"

    def before_agent(
        self, state: AgentState[Any], runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        """同步入口，兼容非流式调用。"""
        self._check_and_recover()
        self._acquire_lease()
        return None

    async def abefore_agent(
        self, state: AgentState[Any], runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        """流式路径在工作线程执行 Docker/Mongo I/O，避免阻塞 FastAPI 事件循环。"""
        await asyncio.to_thread(self._check_and_recover)
        self._acquire_lease()
        return None

    def after_agent(
        self, state: AgentState[Any], runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        self._release_lease()
        return None

    async def aafter_agent(
        self, state: AgentState[Any], runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        self._release_lease()
        return None

    def _acquire_lease(self) -> None:
        """标记本 run 正在进行，避免 idle 清理误删长任务容器。"""
        if self._sandbox_manager is None:
            return
        with self._lease_lock:
            acquire = getattr(self._sandbox_manager, "acquire_lease", None)
            if callable(acquire) and acquire(self._user_id):
                self._lease_depth += 1

    def _release_lease(self) -> None:
        if self._sandbox_manager is None:
            return
        with self._lease_lock:
            if self._lease_depth <= 0:
                return
            release = getattr(self._sandbox_manager, "release_lease", None)
            if callable(release):
                release(self._user_id)
            self._lease_depth -= 1

    def _check_and_recover(self) -> None:
        # LocalShell fallback 没有 Manager，保持原有开发模式语义。
        if self._sandbox_manager is None or self._sandbox_backend is None:
            self._healthy = True
            return

        now = time.monotonic()
        if self._healthy and now - self._last_check < self._check_interval:
            return
        self._last_check = now

        if self._ping_sandbox():
            self._healthy = True
            self._consecutive_failures = 0
            touch = getattr(self._sandbox_manager, "touch", None)
            if callable(touch):
                touch(self._user_id)
            middleware_logger.debug(
                f"Sandbox health check OK for user {self._user_id}"
            )
            return

        self._healthy = False
        self._consecutive_failures += 1
        middleware_logger.warning(
            "Sandbox health check failed for "
            f"{self._user_id} ({self._consecutive_failures} consecutive)"
        )
        if self._consecutive_failures < self._failure_threshold:
            return

        self._rebuild_sandbox()

    def _rebuild_sandbox(self) -> None:
        """重建、热替换并重新填充沙箱；任一环节失败都会在下一次请求重试。"""
        try:
            middleware_logger.info(
                f"Rebuilding unhealthy sandbox for user {self._user_id}"
            )
            replacement = self._sandbox_manager.rebuild(self._user_id)

            replace_backend = getattr(self._sandbox_backend, "replace_backend", None)
            if not callable(replace_backend):
                raise RuntimeError("managed sandbox backend does not support hot replacement")
            replace_backend(replacement)

            if self._on_rebuild is not None:
                self._on_rebuild(replacement)

            if not self._ping_sandbox():
                raise RuntimeError("replacement sandbox did not become healthy")

            touch = getattr(self._sandbox_manager, "touch", None)
            if callable(touch):
                touch(self._user_id)
            self._healthy = True
            self._consecutive_failures = 0
            middleware_logger.info(
                f"Sandbox recovery complete for user {self._user_id}"
            )
        except Exception as exc:
            self._healthy = False
            middleware_logger.error(
                f"Sandbox recovery failed for {self._user_id}: {exc}",
                exc_info=True,
            )
            # 健康检查失败不应让中间件静默继续把请求交给失效容器。
            raise RuntimeError(f"Sandbox recovery failed: {exc}") from exc

    def _ping_sandbox(self) -> bool:
        """优先通过稳定 Proxy 探测当前底层容器。"""
        try:
            ping = getattr(self._sandbox_backend, "ping", None)
            if callable(ping):
                return bool(ping())
            response = self._sandbox_backend.execute("echo ok", timeout=5)
            return response.exit_code == 0
        except Exception:
            return False

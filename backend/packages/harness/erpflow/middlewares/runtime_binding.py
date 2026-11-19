"""把当前 run 的用户、Store、沙箱绑定到工具调用上下文。

工具对象在 Agent 构建时创建、在多次 run 之间复用；用户级信息不能闭包进
工具实例。LangGraph 的 ``ToolCallRequest`` 带有本次 run 的 ``runtime``，因此
在 ``wrap_tool_call`` 的最外层绑定 ContextVar，并在 ``finally`` 中复位：

- ``bind_sandbox``：当前用户的稳定 Proxy（没有就显式绑定 None，禁止回退到
  default_user）
- ``bind_runtime``：user_id + Store

钩子只包住 handler，所以并发的工具调用各自持有自己的上下文；子 Agent 也是
作为工具被调用，其子任务会继承父调用的 ContextVar。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..backends.sandbox_holder import bind_sandbox, reset_sandbox, set_sandbox
from ..runtime_context import bind_runtime, reset_runtime

ToolResult = ToolMessage | Command[Any]
ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolResult]]


class RuntimeBindingMiddleware(AgentMiddleware):
    """为每次工具调用绑定 run-scoped 的 user/store/sandbox。"""

    def __init__(
        self,
        user_id: str,
        store: Any = None,
        sandbox: Any = None,
    ) -> None:
        self._user_id = user_id
        self._store = store
        self.tools = []
        # 构建 Agent 时注册稳定 Proxy；工具调用时仍会按 user_id 重新绑定。
        if sandbox is not None:
            set_sandbox(sandbox, user_id)

    @property
    def name(self) -> str:
        return "RuntimeBindingMiddleware"

    def _bind(self, request: ToolCallRequest):
        runtime = getattr(request, "runtime", None)
        store = getattr(runtime, "store", None)
        if store is None:
            store = self._store
        return bind_runtime(self._user_id, store), bind_sandbox(self._user_id)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        runtime_tokens, sandbox_binding = self._bind(request)
        try:
            return handler(request)
        finally:
            reset_sandbox(sandbox_binding)
            reset_runtime(runtime_tokens)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolResult:
        runtime_tokens, sandbox_binding = self._bind(request)
        try:
            return await handler(request)
        finally:
            reset_sandbox(sandbox_binding)
            reset_runtime(runtime_tokens)


__all__ = ["RuntimeBindingMiddleware"]
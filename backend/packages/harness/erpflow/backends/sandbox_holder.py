"""请求级沙箱绑定。

工具函数（图表、文档、Skill 安装器）不能把某个用户的容器保存在普通模块全局中：
FastAPI 的多个 SSE 请求会并发执行，普通全局会造成 A 用户的工具写入 B 用户沙箱。

这里同时维护用户 → 稳定 Proxy 的注册表和 ``ContextVar``。Agent 创建时注册 Proxy；
每次工具调用前绑定它，工具在当前协程上下文中取到正确实例。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sandbox_proxy import SandboxBackendProxy

DEFAULT_USER_ID = "default_user"
_request_sandbox: ContextVar[SandboxBackendProxy | None] = ContextVar(
    "erp_agent_request_sandbox", default=None
)
# 当前请求绑定的用户。与 sandbox 分开保存，这样即使该用户没有沙箱，
# get_sandbox() 也不会回退到 default_user 的注册表项。
_request_user_id: ContextVar[str | None] = ContextVar(
    "erp_agent_request_user_id", default=None
)
_sandboxes_by_user: dict[str, SandboxBackendProxy] = {}
_registry_lock = RLock()


@dataclass(frozen=True)
class SandboxBinding:
    """``bind_sandbox`` 产生的两个 ContextVar token。"""

    sandbox: Token[SandboxBackendProxy | None]
    user_id: Token[str | None]


def set_sandbox(
    sandbox: SandboxBackendProxy | None, user_id: str = DEFAULT_USER_ID
) -> None:
    """注册或移除某位用户的稳定沙箱 Proxy。"""
    with _registry_lock:
        if sandbox is None:
            _sandboxes_by_user.pop(user_id, None)
        else:
            _sandboxes_by_user[user_id] = sandbox


def get_sandbox(user_id: str | None = None) -> SandboxBackendProxy | None:
    """获取当前请求的沙箱；非请求上下文时可按用户查注册表。

    显式绑定过用户后，即使该用户没有沙箱也返回 ``None``，绝不回退到
    ``default_user`` 的沙箱——否则并发请求会把 A 用户的工具落到 B 的容器。
    """
    bound_user = _request_user_id.get()
    if bound_user is not None and (user_id is None or user_id == bound_user):
        return _request_sandbox.get()

    # 没有绑定用户时，兼容旧的直接 set ContextVar 的调用方式。
    if bound_user is None:
        sandbox = _request_sandbox.get()
        if sandbox is not None:
            return sandbox

    with _registry_lock:
        return _sandboxes_by_user.get(user_id or DEFAULT_USER_ID)


def bind_sandbox(user_id: str) -> SandboxBinding:
    """将某位用户的沙箱绑定到当前协程，返回可在 finally 中复位的 token。"""
    with _registry_lock:
        sandbox = _sandboxes_by_user.get(user_id)
    return SandboxBinding(
        sandbox=_request_sandbox.set(sandbox),
        user_id=_request_user_id.set(user_id),
    )


def reset_sandbox(binding: SandboxBinding) -> None:
    """结束请求时恢复上层 ContextVar 值。"""
    _request_user_id.reset(binding.user_id)
    _request_sandbox.reset(binding.sandbox)


def has_sandbox(user_id: str | None = None) -> bool:
    """检查当前请求（或指定用户）的沙箱是否可用。"""
    sandbox = get_sandbox(user_id)
    if sandbox is None:
        return False
    try:
        return bool(sandbox.ping())
    except Exception:
        return False
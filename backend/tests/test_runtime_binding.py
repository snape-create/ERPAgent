"""P0-2：工具调用时的 run-scoped user/store/sandbox 绑定。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from erpflow.backends.sandbox_holder import (
    bind_sandbox,
    get_sandbox,
    reset_sandbox,
    set_sandbox,
)
from erpflow.middlewares.runtime_binding import RuntimeBindingMiddleware
from erpflow.runtime_context import get_current_store, get_current_user_id


class DummySandbox:
    def __init__(self, name: str):
        self.name = name


def _request(store=None):
    return SimpleNamespace(runtime=SimpleNamespace(store=store))


def test_sync_tool_call_binds_and_resets_context():
    alice = DummySandbox("alice")
    set_sandbox(alice, "alice")
    try:
        middleware = RuntimeBindingMiddleware("alice", store="build-store")
        run_store = object()
        seen = {}

        def handler(_request):
            seen["sandbox"] = get_sandbox()
            seen["user"] = get_current_user_id()
            seen["store"] = get_current_store()
            return "ok"

        assert middleware.wrap_tool_call(_request(run_store), handler) == "ok"
        assert seen["sandbox"] is alice
        assert seen["user"] == "alice"
        assert seen["store"] is run_store

        # 调用结束后必须复位，不能污染下一个工具/请求。
        assert get_sandbox() is None
        assert get_current_user_id() == "default_user"
        assert get_current_store() is None
    finally:
        set_sandbox(None, "alice")


def test_async_tool_call_resets_even_on_exception():
    alice = DummySandbox("alice")
    set_sandbox(alice, "alice")

    async def run():
        middleware = RuntimeBindingMiddleware("alice", store="build-store")

        async def handler(_request):
            assert get_sandbox() is alice
            assert get_current_user_id() == "alice"
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await middleware.awrap_tool_call(_request(), handler)

        assert get_sandbox() is None
        assert get_current_user_id() == "default_user"
        assert get_current_store() is None

    try:
        asyncio.run(run())
    finally:
        set_sandbox(None, "alice")


def test_bound_user_without_sandbox_does_not_fall_back_to_default_user():
    default = DummySandbox("default")
    set_sandbox(default, "default_user")
    try:
        binding = bind_sandbox("alice-no-sandbox")
        try:
            # 显式绑定 alice 后即使 alice 没有沙箱，也不能拿到 default_user 的。
            assert get_sandbox() is None
        finally:
            reset_sandbox(binding)

        assert get_sandbox() is default
    finally:
        set_sandbox(None, "default_user")


def test_runtime_store_preferred_over_build_store():
    middleware = RuntimeBindingMiddleware("u1", store="build-store")
    run_store = object()
    seen = {}

    def handler(_request):
        seen["store"] = get_current_store()
        return "ok"

    middleware.wrap_tool_call(_request(run_store), handler)
    assert seen["store"] is run_store


def test_build_store_used_when_runtime_store_missing():
    middleware = RuntimeBindingMiddleware("u2", store="build-store")
    seen = {}

    def handler(_request):
        seen["store"] = get_current_store()
        return "ok"

    middleware.wrap_tool_call(_request(None), handler)
    assert seen["store"] == "build-store"
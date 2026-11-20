"""图工厂回归：按用户缓存 + 阻塞装配只发生在 executor 线程。"""
from __future__ import annotations

import os
import threading

import pytest

from erpflow import graph as graph_module
from erpflow.agent import graph_cache


@pytest.fixture(autouse=True)
def reset_graph_cache():
    """每个用例从空缓存、未预热状态开始。"""
    graph_cache._graphs.clear()
    graph_cache._pending.clear()
    graph_cache._warmup_started = False
    graph_cache._warmup_done.clear()
    graph_cache._pool_ready = False
    yield
    graph_cache._graphs.clear()
    graph_cache._pending.clear()


def _patch_factory(monkeypatch, builder):
    monkeypatch.setattr("erpflow.agent.build.create_main_agent", builder)
    monkeypatch.setattr(
        "erpflow.tools.mcp_client.load_mcp_tools_sync", lambda *a, **k: []
    )
    monkeypatch.setattr("erpflow.store.get_store", lambda: object())
    monkeypatch.setattr(graph_cache, "ensure_sandbox_pool_once", lambda: None)


def test_same_user_reuses_graph(monkeypatch):
    built: list[str] = []

    def fake_create(user_context=None, checkpointer=None, store=None, **kwargs):
        built.append(user_context.user_id)
        return object()

    _patch_factory(monkeypatch, fake_create)

    first = graph_module.make_lead_agent(
        {"configurable": {"user_id": "alice", "username": "爱丽丝"}}
    )
    second = graph_module.make_lead_agent(
        {"configurable": {"user_id": "alice", "username": "爱丽丝"}}
    )
    other = graph_module.make_lead_agent(
        {"configurable": {"user_id": "bob", "username": "鲍勃"}}
    )

    assert first is second
    assert other is not first
    assert built == ["alice", "bob"]


def test_missing_user_builds_sandboxless_readonly_graph(monkeypatch):
    calls: list[tuple[str, bool]] = []

    def fake_create(user_context=None, checkpointer=None, store=None, **kwargs):
        calls.append((user_context.user_id, kwargs.get("enable_sandbox", True)))
        return object()

    _patch_factory(monkeypatch, fake_create)

    first = graph_module.make_lead_agent(None)
    second = graph_module.make_lead_agent({"configurable": {}})
    assert first is second
    assert calls == [(graph_module.READONLY_USER_ID, False)]


def test_execution_with_user_id_enables_sandbox(monkeypatch):
    calls: list[tuple[str, bool]] = []

    def fake_create(user_context=None, checkpointer=None, store=None, **kwargs):
        calls.append((user_context.user_id, kwargs.get("enable_sandbox", True)))
        return object()

    _patch_factory(monkeypatch, fake_create)

    graph_module.make_lead_agent({"configurable": {"user_id": "frank"}})
    assert calls == [("frank", True)]


def test_blocking_work_runs_in_executor_thread(monkeypatch):
    calls: list[tuple[str, str]] = []
    main_thread = threading.current_thread()

    def record(name: str) -> None:
        calls.append((name, threading.current_thread().name))

    real_getcwd = os.getcwd

    def fake_getcwd():
        record("os.getcwd")
        return real_getcwd()

    def fake_create(user_context=None, checkpointer=None, store=None, **kwargs):
        fake_getcwd()
        record("create_main_agent")
        return object()

    monkeypatch.setattr(os, "getcwd", fake_getcwd)
    monkeypatch.setattr("erpflow.agent.build.create_main_agent", fake_create)
    monkeypatch.setattr(
        "erpflow.tools.mcp_client.load_mcp_tools_sync",
        lambda *a, **k: record("load_mcp_tools_sync") or [],
    )
    monkeypatch.setattr(
        "erpflow.store.get_store", lambda: record("get_store") or object()
    )
    monkeypatch.setattr(
        graph_cache, "ensure_sandbox_pool_once", lambda: record("sandbox_pool")
    )

    graph_module.make_lead_agent({"configurable": {"user_id": "carol"}})

    assert {name for name, _ in calls} == {
        "load_mcp_tools_sync",
        "get_store",
        "sandbox_pool",
        "create_main_agent",
        "os.getcwd",
    }
    # 所有阻塞入口都只在 graph-build 线程里执行，事件循环线程一次都没碰。
    assert all(thread != main_thread.name for _, thread in calls)
    assert all(thread.startswith("graph-build") for _, thread in calls)


def test_warmup_runs_once_and_failure_is_non_fatal(monkeypatch):
    attempts: list[int] = []

    def failing_mcp(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("mcp unavailable")

    def fake_create(user_context=None, checkpointer=None, store=None, **kwargs):
        return object()

    monkeypatch.setattr("erpflow.agent.build.create_main_agent", fake_create)
    monkeypatch.setattr(
        "erpflow.tools.mcp_client.load_mcp_tools_sync", failing_mcp
    )
    monkeypatch.setattr("erpflow.store.get_store", lambda: object())
    monkeypatch.setattr(graph_cache, "ensure_sandbox_pool_once", lambda: None)

    first = graph_module.make_lead_agent({"configurable": {"user_id": "dave"}})
    second = graph_module.make_lead_agent({"configurable": {"user_id": "erin"}})

    assert first is not None and second is not None
    assert first is not second
    assert len(attempts) == 1

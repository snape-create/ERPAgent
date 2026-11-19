"""SandboxManager 状态机：租约、空闲回收、锁外销毁。"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

from erpflow.backends.sandbox_entry import SandboxEntry, SandboxState
from erpflow.backends.sandbox_manager import SandboxManager


class FakeSandbox:
    def __init__(self, name: str):
        self.container_name = name
        self.container_id = f"id-{name}"
        self.destroyed = 0

    def destroy_container(self):
        self.destroyed += 1

    def ping(self) -> bool:
        return True


class FakeCache:
    def __init__(self):
        self.deleted: list[str] = []

    def delete(self, user_id: str) -> None:
        self.deleted.append(user_id)


def _manager(cache=None):
    manager = object.__new__(SandboxManager)
    manager._lock = threading.Lock()
    manager._entries = {}
    manager._user_map = {}
    manager._warm_creating = 0
    manager._warm_pool_size = 0
    manager._idle_timeout = timedelta(minutes=30)
    manager._cache = cache or FakeCache()
    return manager


def _entry(manager, user_id: str, *, age_minutes: int, active_runs: int = 0):
    sandbox = FakeSandbox(f"c-{user_id}")
    entry = SandboxEntry(sandbox, user_id=user_id, state=SandboxState.CLAIMED)
    entry.last_active_at = datetime.now() - timedelta(minutes=age_minutes)
    entry.active_runs = active_runs
    manager._entries[entry.container_name] = entry
    manager._user_map[user_id] = entry.container_name
    return entry


def test_cleanup_idle_destroys_only_expired_idle_entries():
    manager = _manager()
    old = _entry(manager, "old", age_minutes=60)
    fresh = _entry(manager, "fresh", age_minutes=1)
    busy = _entry(manager, "busy", age_minutes=60, active_runs=1)

    manager.cleanup_idle_sandboxes()

    assert old.sandbox.destroyed == 1
    assert fresh.sandbox.destroyed == 0
    assert busy.sandbox.destroyed == 0
    assert old.container_name not in manager._entries
    assert manager._user_map.get("old") is None
    assert manager._cache.deleted == ["old"]


def test_acquire_and_release_lease_roundtrip():
    manager = _manager()
    entry = _entry(manager, "alice", age_minutes=0)

    assert manager.acquire_lease("alice") is True
    assert entry.active_runs == 1
    manager.release_lease("alice")
    assert entry.active_runs == 0
    # 不存在的用户不会把计数变负。
    manager.release_lease("missing")
    assert entry.active_runs == 0


def test_destroy_user_sandbox_destroys_container_and_clears_cache():
    manager = _manager()
    entry = _entry(manager, "bob", age_minutes=0)

    manager.destroy_user_sandbox("bob")

    assert entry.sandbox.destroyed == 1
    assert "bob" not in manager._user_map
    assert entry.container_name not in manager._entries
    assert manager._cache.deleted == ["bob"]


def test_destroy_all_destroys_every_container():
    manager = _manager()
    a = _entry(manager, "a", age_minutes=0)
    b = _entry(manager, "b", age_minutes=0)

    manager.destroy_all()

    assert a.sandbox.destroyed == 1
    assert b.sandbox.destroyed == 1
    assert manager._entries == {}
    assert manager._user_map == {}
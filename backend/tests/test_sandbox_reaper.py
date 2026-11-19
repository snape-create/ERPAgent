"""启动期孤儿容器清理。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from erpflow.backends import sandbox_reaper


class FakeContainer:
    def __init__(self, name: str, age_minutes: float):
        created = datetime.now(UTC) - timedelta(minutes=age_minutes)
        self.name = name
        self.attrs = {"Created": created.isoformat()}
        self.removed = 0

    def remove(self, force: bool = False):
        assert force is True
        self.removed += 1


class FakeContainers:
    def __init__(self, containers):
        self._containers = containers

    def list(self, filters=None):
        return self._containers


class FakeClient:
    def __init__(self, containers):
        self.containers = FakeContainers(containers)
        self.closed = False

    def close(self):
        self.closed = True


class FakeCache:
    def __init__(self, protected):
        self._protected = protected

    def protected_container_names(self):
        return self._protected


def test_removes_only_old_unprotected_user_containers(monkeypatch):
    old = FakeContainer("erp-sandbox-alice-abc123", age_minutes=60)
    recent = FakeContainer("erp-sandbox-bob-def456", age_minutes=1)
    protected = FakeContainer("erp-sandbox-carol-ghi789", age_minutes=60)
    warm = FakeContainer("erp-sandbox-warm-xyz", age_minutes=60)
    client = FakeClient([old, recent, protected, warm])
    monkeypatch.setattr(sandbox_reaper.docker, "from_env", lambda timeout=15: client)
    cache = FakeCache({protected.name})

    removed = sandbox_reaper.cleanup_stale_user_containers(
        cache=cache, min_age_minutes=10
    )

    assert removed == 1
    assert old.removed == 1
    assert recent.removed == 0
    assert protected.removed == 0
    assert warm.removed == 0
    assert client.closed is True


def test_skips_when_cache_unavailable(monkeypatch):
    monkeypatch.setattr(
        sandbox_reaper.docker,
        "from_env",
        lambda timeout=15: (_ for _ in ()).throw(AssertionError("must not connect")),
    )
    assert sandbox_reaper.cleanup_stale_user_containers(cache=FakeCache(None)) == 0

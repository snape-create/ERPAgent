"""启动期孤儿清理：扫掉上次进程遗留的空闲 warm 容器。

与运行期状态机（``SandboxManager``）分开：这里不持有任何内存状态，
只在进程启动时对 Docker 做一次扫描，判断依据只有命名前缀 + MongoDB 缓存。
"""
from datetime import UTC, datetime

import docker

from ..log_utils import sandbox_logger
from .sandbox_cache import sandbox_cache
from .sandbox_entry import USER_NAME_PREFIX, WARM_NAME_PREFIX


def cleanup_stale_warm(cache=None) -> int:
    """删除未在缓存中登记的空闲 warm 容器，返回删除数量。

    Ctrl+C / 强杀时进程来不及执行关停钩子，warm 容器会留在 docker 里，
    新进程又建一批，越积越多。这里在启动阶段扫一次：
    只删缓存里没有登记的 warm（登记过的是已认领的用户容器，不能删）。
    缓存读不到就跳过，宁可留容器也不误删用户数据。
    """
    cache = cache or sandbox_cache
    protected = cache.protected_container_names()
    if protected is None:
        return 0

    removed = 0
    try:
        docker_client = docker.from_env(timeout=15)
        for container in docker_client.containers.list(filters={"name": WARM_NAME_PREFIX}):
            name = getattr(container, "name", "") or ""
            if not name.startswith(WARM_NAME_PREFIX) or name in protected:
                continue
            container.remove(force=True)
            removed += 1
            sandbox_logger.info(f"Removed stale warm sandbox: {name}")
        docker_client.close()
    except Exception as exc:
        sandbox_logger.warning(f"Stale warm cleanup failed: {exc}")

    if removed:
        sandbox_logger.info(f"Removed {removed} stale warm sandbox container(s)")
    return removed


def _container_age_minutes(container) -> float | None:
    """容器创建时间距今分钟数；时间解析失败返回 None（保守跳过）。"""
    raw = (getattr(container, "attrs", {}) or {}).get("Created")
    if not raw:
        return None
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (datetime.now(UTC) - created).total_seconds() / 60.0
    except ValueError:
        return None


def cleanup_stale_user_containers(cache=None, min_age_minutes: float = 10.0) -> int:
    """删除未登记在 MongoDB、且已超过安全年龄的用户容器。

    进程被杀时可能在 ``_persist`` 之前留下 ``erp-sandbox-<user>-*``。这里只删
    缓存中没有、且创建超过 ``min_age_minutes`` 的容器，给其他进程的持久化留出
    窗口，避免误删刚创建的用户容器。
    """
    cache = cache or sandbox_cache
    protected = cache.protected_container_names()
    if protected is None:
        return 0

    removed = 0
    try:
        docker_client = docker.from_env(timeout=15)
        for container in docker_client.containers.list(filters={"name": USER_NAME_PREFIX}):
            name = getattr(container, "name", "") or ""
            if not name.startswith(f"{USER_NAME_PREFIX}-") or name.startswith(WARM_NAME_PREFIX):
                continue
            if name in protected:
                continue
            age = _container_age_minutes(container)
            if age is None or age < min_age_minutes:
                continue
            container.remove(force=True)
            removed += 1
            sandbox_logger.info(f"Removed stale user sandbox: {name}")
        docker_client.close()
    except Exception as exc:
        sandbox_logger.warning(f"Stale user sandbox cleanup failed: {exc}")

    if removed:
        sandbox_logger.info(f"Removed {removed} stale user sandbox container(s)")
    return removed

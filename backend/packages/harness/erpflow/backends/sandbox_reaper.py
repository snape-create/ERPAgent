"""启动期孤儿清理：扫掉上次进程遗留的空闲 warm 容器。

与运行期状态机（``SandboxManager``）分开：这里不持有任何内存状态，
只在进程启动时对 Docker 做一次扫描，判断依据只有命名前缀 + MongoDB 缓存。
"""
import docker

from ..log_utils import sandbox_logger
from .sandbox_cache import sandbox_cache
from .sandbox_entry import WARM_NAME_PREFIX


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

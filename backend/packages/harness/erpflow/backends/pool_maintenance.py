"""沙箱预热池的后台维护。

容器池必须跟着「Agent 所在的进程」走：模型调用发生在哪个进程，池就在哪维护。
所以这段逻辑属于 harness，而不是网关——网关切到 LangGraph Server 之后
它手里根本没有沙箱。
"""
import threading
import time

from ..config import (
    SANDBOX_MAINTENANCE_INTERVAL_SECONDS,
    SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS,
)
from ..log_utils import sandbox_logger
from .sandbox_manager import sandbox_manager
from .sandbox_reaper import cleanup_stale_user_containers, cleanup_stale_warm
from .user_data_cleanup import cleanup_expired_user_data

_started = False
_lock = threading.Lock()


def _loop(stop_event: threading.Event) -> None:
    last_cleanup = 0.0
    while not stop_event.is_set():
        try:
            sandbox_manager.cleanup_idle_sandboxes()
            sandbox_manager.ensure_warm_pool()

            now = time.monotonic()
            if now - last_cleanup >= SANDBOX_USER_DATA_CLEANUP_INTERVAL_SECONDS:
                cleanup_expired_user_data(
                    active_user_ids=sandbox_manager.active_user_ids()
                )
                last_cleanup = now
        except Exception as exc:
            sandbox_logger.warning(f"Sandbox maintenance iteration failed: {exc}")
        if stop_event.wait(SANDBOX_MAINTENANCE_INTERVAL_SECONDS):
            break


def start_maintenance() -> threading.Event:
    """启动一次预热补池 + 常驻维护线程；重复调用无副作用。"""
    global _started
    stop_event = threading.Event()
    with _lock:
        if _started:
            return stop_event
        _started = True

    try:
        # 上次进程可能被强杀；先扫掉未登记的 warm/用户容器，再补池
        cleanup_stale_warm()
        cleanup_stale_user_containers()
        sandbox_manager.ensure_warm_pool()
    except Exception as exc:
        sandbox_logger.warning(f"Initial warm pool fill failed: {exc}")

    threading.Thread(target=_loop, args=(stop_event,), name="sandbox-maintenance", daemon=True).start()
    sandbox_logger.info("Sandbox maintenance thread started")
    return stop_event


def stop_maintenance(stop_event: threading.Event) -> None:
    """停止维护线程（网关内嵌模式可用；Server 模式靠下次启动的孤儿清理）。"""
    stop_event.set()

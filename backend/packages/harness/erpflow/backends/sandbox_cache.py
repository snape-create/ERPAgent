"""沙箱映射持久化：MongoDB ``sandbox_cache`` 集合（user_id → container）。

服务重启后靠这张表恢复用户容器，避免把已认领容器当孤儿删掉。
持久化失败只降级（打 warning），绝不影响主流程：沙箱缓存丢了顶多重建容器。

每次操作独立开关 client：这些调用都在锁外、低频（认领/恢复/回收），
共享长连接反而要在进程关停时处理更多生命周期问题。
"""
from ..config import MONGODB_DB_NAME, MONGODB_SELECTION_TIMEOUT_MS, MONGODB_URI
from ..log_utils import sandbox_logger

# 连接参数统一来自 settings（历史上这四处各读一次 os.getenv，默认值还不一致）
MONGO_SELECTION_TIMEOUT_MS = MONGODB_SELECTION_TIMEOUT_MS

COLLECTION_NAME = "sandbox_cache"


class SandboxCache:
    """``sandbox_cache`` 集合的读写封装。"""

    def __init__(
        self,
        uri: str | None = None,
        db_name: str | None = None,
        timeout_ms: int = MONGO_SELECTION_TIMEOUT_MS,
    ):
        self._uri = uri or MONGODB_URI
        self._db_name = db_name or MONGODB_DB_NAME
        self._timeout_ms = timeout_ms

    def _connect(self):
        """返回 (client, collection)；调用方负责 close。"""
        from pymongo import MongoClient

        client = MongoClient(self._uri, serverSelectionTimeoutMS=self._timeout_ms)
        return client, client[self._db_name][COLLECTION_NAME]

    def persist(self, payload: dict) -> bool:
        """按 user_id upsert 一条映射；成功返回 True。"""
        user_id = payload.get("user_id")
        if not user_id:
            return False
        try:
            client, collection = self._connect()
            try:
                collection.update_one({"user_id": user_id}, {"$set": payload}, upsert=True)
            finally:
                client.close()
            sandbox_logger.debug(f"Persisted sandbox cache for {user_id}")
            return True
        except Exception as exc:
            sandbox_logger.warning(f"Failed to persist sandbox cache: {exc}")
            return False

    def find(self, user_id: str) -> dict | None:
        """查询用户的映射；读不到返回 None（调用方会退回预热池/新建）。"""
        try:
            client, collection = self._connect()
            try:
                return collection.find_one({"user_id": user_id})
            finally:
                client.close()
        except Exception as exc:
            sandbox_logger.warning(f"Failed to read sandbox cache: {exc}")
            return None

    def delete(self, user_id: str) -> None:
        """删除一条失效映射。"""
        try:
            client, collection = self._connect()
            try:
                collection.delete_one({"user_id": user_id})
            finally:
                client.close()
        except Exception as exc:
            sandbox_logger.warning(f"Failed to cleanup MongoDB cache: {exc}")

    def protected_container_names(self) -> set[str] | None:
        """所有已登记容器名。

        返回 None 表示缓存读不到 —— 调用方必须放弃清理，
        宁可留几个空闲容器，也不能误删用户数据所在的容器。
        """
        try:
            client, collection = self._connect()
            try:
                docs = collection.find({}, {"container_name": 1})
                return {
                    doc["container_name"] for doc in docs if doc.get("container_name")
                }
            finally:
                client.close()
        except Exception as exc:
            sandbox_logger.warning(f"Skip stale warm cleanup, cache unreadable: {exc}")
            return None


sandbox_cache = SandboxCache()

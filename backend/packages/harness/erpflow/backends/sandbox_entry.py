"""沙箱条目：状态枚举 + 元数据 + 容器创建。

把「怎么造一个沙箱容器」收敛到一处：预热容器与用户容器只差一个名字，
目录初始化、运行时选择、安全配置都必须一致，否则两类容器行为会漂移。
"""
import uuid
from datetime import datetime

from ..config import (
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_PYTHON_PACKAGES_DIR,
    SANDBOX_SKILLS_DIR,
    SANDBOX_UPLOADS_DIR,
    SANDBOX_USER_DATA_MOUNTS,
    SANDBOX_WORK_DIR,
)
from .custom_opensandbox import CustomOpenSandbox
from .sandbox_setup import SandboxConfig, create_secure_sandbox
from .user_data import user_data_mounts

# 容器内统一预建的目录（工作区/产物/技能/依赖）
WORKSPACE_DIRS = (
    SANDBOX_WORK_DIR,
    f"{SANDBOX_WORK_DIR}/data",
    f"{SANDBOX_WORK_DIR}/analysis",
    SANDBOX_OUTPUTS_DIR,
    SANDBOX_UPLOADS_DIR,
    SANDBOX_PYTHON_PACKAGES_DIR,
    SANDBOX_SKILLS_DIR,
    # 兼容旧路径，迁移完成后可删。
    "/workspace",
)

WARM_NAME_PREFIX = "erp-sandbox-warm-"
USER_NAME_PREFIX = "erp-sandbox"


class SandboxState:
    """沙箱五态。"""

    WARM = "warm"           # 预热池中的空闲容器
    CLAIMED = "claimed"     # 已分配给用户
    CACHED = "cached"       # 已持久化到 MongoDB
    CREATING = "creating"   # 正在创建中
    DESTROYED = "destroyed"  # 已销毁


def warm_container_name() -> str:
    return f"{WARM_NAME_PREFIX}{uuid.uuid4().hex[:12]}"


def user_container_name(user_id: str) -> str:
    """容器名只允许字母数字与下划线，避免非法字符进 docker 名称。"""
    safe_user_id = "".join(c if c.isalnum() else "_" for c in user_id)
    return f"{USER_NAME_PREFIX}-{safe_user_id}-{uuid.uuid4().hex[:12]}"


def create_sandbox_container(name: str, user_id: str = "") -> CustomOpenSandbox:
    """创建一个带安全防护的沙箱容器，并预建标准目录。

    用户容器会挂载 ``user-data/{uploads,outputs}``；预热容器没有用户，
    不挂载任何用户数据。启用 user-data 挂载时 warm pool 会被停用。
    """
    volumes = user_data_mounts(user_id) if (SANDBOX_USER_DATA_MOUNTS and user_id) else {}
    # runtimes 使用配置默认值（[sandbox.container].runtimes），不再静默忽略。
    sandbox = create_secure_sandbox(SandboxConfig(name=name, volumes=volumes))
    sandbox.execute("mkdir -p " + " ".join(WORKSPACE_DIRS))
    return sandbox


class SandboxEntry:
    """沙箱条目（跟踪容器的完整生命周期）。"""

    def __init__(
        self,
        sandbox: CustomOpenSandbox,
        user_id: str = "",
        state: str = SandboxState.WARM,
    ):
        self.sandbox = sandbox
        self.user_id = user_id
        self.state = state
        self.created_at = datetime.now()
        self.claimed_at: datetime | None = None
        self.last_active_at = datetime.now()
        self.active_runs = 0
        # 最近一次 acquire/release 的时间；用于识别 HITL/断流后未释放的孤儿租约。
        self.lease_last_updated: datetime | None = None

    def touch(self) -> None:
        """更新活跃时间。"""
        self.last_active_at = datetime.now()

    @property
    def container_name(self) -> str:
        return self.sandbox.container_name

    @property
    def container_id(self) -> str:
        return self.sandbox.container_id

    def is_healthy(self) -> bool:
        """检查容器是否健康。"""
        return self.sandbox.ping()

    def to_dict(self) -> dict:
        """序列化为字典（用于 MongoDB 存储）。"""
        return {
            "container_name": self.container_name,
            "container_id": self.container_id,
            "user_id": self.user_id,
            "state": self.state,
            "created_at": self.created_at.isoformat(),
            "claimed_at": self.claimed_at.isoformat() if self.claimed_at else None,
            "last_active_at": self.last_active_at.isoformat(),
            "active_runs": self.active_runs,
            "lease_last_updated": (
                self.lease_last_updated.isoformat()
                if self.lease_last_updated
                else None
            ),
        }

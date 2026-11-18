"""技能（Skills）加载与渐进披露。

数据资产不在这里：技能文件在 ``backend/skills/``，沙箱内挂在 ``/skills``。
本包只负责发现、解析、按需读取与中间件接入。
"""
from .progressive import (
    CATALOG_LIMIT,
    DOCUMENT_LIMIT,
    SLOT,
    ProgressiveSkillsMiddleware,
    SkillCatalog,
    attach_to_subagents,
    metadata,
    safe_relative,
)

__all__ = [
    "CATALOG_LIMIT", "DOCUMENT_LIMIT", "SLOT", "ProgressiveSkillsMiddleware",
    "SkillCatalog", "attach_to_subagents", "metadata", "safe_relative",
]

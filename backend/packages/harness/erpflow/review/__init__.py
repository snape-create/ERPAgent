"""评审与阶段机（原 harness.py / review_policy.py / jev_router.py）。

三者是同一个关注点：判断"这一轮要不要评审、评审什么、评审结果如何影响状态机"。

    phase.py   阶段状态机 + rubric 注入（HarnessPhaseMiddleware）
    policy.py  路由策略与 rubric 组装（ReviewPolicy / ReviewRoute）
    jev.py     外部决策模型适配器（JevRouter）
    config.yaml 审查策略 DSL（mode / strategy / 超时 / 评分维度）
"""
from .jev import TASKS, JevRouter, build_review_router
from .phase import (
    HARNESS_CONFIG_PATH,
    HarnessPhaseMiddleware,
    HarnessPhaseState,
    build_rubric,
    detect_task_type,
    latest_human_text,
    load_harness_config,
    should_use_grader,
)
from .policy import CHECKS, ROUTER_PROMPT, ReviewPolicy, ReviewRoute, compose_rubric, reset_grading

__all__ = [
    "TASKS", "JevRouter", "build_review_router",
    "HARNESS_CONFIG_PATH", "HarnessPhaseMiddleware", "HarnessPhaseState",
    "build_rubric", "detect_task_type", "latest_human_text", "load_harness_config",
    "should_use_grader",
    "CHECKS", "ROUTER_PROMPT", "ReviewPolicy", "ReviewRoute", "compose_rubric",
    "reset_grading",
]

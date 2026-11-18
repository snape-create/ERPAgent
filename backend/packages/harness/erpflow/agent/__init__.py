"""Agent 装配与沙箱引导。

    build.py      create_main_agent / precompute_agent_context
    bootstrap.py  沙箱引导（技能 + 源码上传）
"""
from .bootstrap import bootstrap_sandbox
from .build import create_main_agent, precompute_agent_context

__all__ = ["create_main_agent", "precompute_agent_context", "bootstrap_sandbox"]

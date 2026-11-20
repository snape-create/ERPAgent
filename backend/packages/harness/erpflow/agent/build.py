"""Agent 装配：LLM + 沙箱后端 + 工具 + 子 Agent + 中间件 + 提示词。

沙箱引导（文件上传）在 :mod:`erpflow.agent.bootstrap`；图入口在
:mod:`erpflow.graph`（LangGraph Server 用 ``make_lead_agent`` 调用本模块）。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend, StoreBackend
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ToolCallLimitMiddleware,
)

from ..config import (
    INTERRUPT_ON_TOOLS,
    MAX_MODEL_CALLS,
    MAX_TOOL_CALLS,
    SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS,
    SKILLS_DIR,
    get_llm,
    skills_store_namespace,
)
from ..log_utils import agent_logger
from ..memory.config import DEFAULT_MEMORY_CONFIG
from ..memory.keeper import MemoryKeeper
from ..memory.prompts import MAIN_SYSTEM_PROMPT, MEMORY_USAGE_PROMPT
from ..schema import ProcurementContext
from .bootstrap import bootstrap_sandbox

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.store.base import BaseStore

MEMORY_DIR = Path(__file__).resolve().parents[1] / "memory"


async def precompute_agent_context(
    user_id: str = "default_user",
    username: str = "用户",
    store: BaseStore | None = None,
) -> ProcurementContext:
    """
    预计算 Agent 上下文

    从 Store 加载用户历史偏好与 WARM 层记忆摘要，构造完整的 ProcurementContext。
    如果 Store 不可用则使用默认值。
    """
    preferences = {}
    warm_memory = ""

    if store is not None:
        try:
            namespace = ("user-preferences", user_id)
            items = store.search(namespace)
            for item in items:
                if item.key == "preferences" and isinstance(item.value, dict):
                    preferences.update(item.value)
            if preferences:
                agent_logger.info(f"Loaded preferences for {user_id}: {list(preferences.keys())}")
        except Exception as e:
            agent_logger.warning(f"Failed to load preferences: {e}")

        # WARM 层：语义记忆（偏好）+ 近期情节摘要，常驻注入系统提示词
        try:
            keeper = MemoryKeeper(store, user_id, DEFAULT_MEMORY_CONFIG)
            keeper.migrate_legacy_preferences()
            warm_memory = keeper.build_warm_brief()
            if not preferences:
                preferences.update(keeper.load_preferences())
        except Exception as e:
            agent_logger.warning(f"Failed to build warm memory: {e}")

    return ProcurementContext(
        user_id=user_id,
        username=username,
        preferences=preferences,
        warm_memory=warm_memory,
    )


def create_main_agent(
    user_context: ProcurementContext | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    *,
    enable_sandbox: bool = True,
):
    """
    创建主 Agent 实例

    组装顺序（严格遵循文档）：
    1. LLM（DeepSeek）
    2. CompositeBackend 三层路由（default=sandbox, /memories/=Store, /persisted-skills/=Store）
    3. 工具加载（MCP + chart + hitl）
    4. 子Agent配置（YAML声明式）
    5. 中间件栈（7自定义 + 框架内置）
    6. 创建 Agent

    Args:
        user_context: 用户上下文（含偏好）
        checkpointer: LangGraph checkpointer（会话持久化）
        store: LangGraph store（跨会话存储）
        enable_sandbox: 是否领取/创建用户沙箱。state 读取等非执行路径应传 False。

    Returns:
        CompiledStateGraph - 可执行的 Agent 图
    """
    if user_context is None:
        user_context = ProcurementContext()

    agent_logger.info(f"Creating main agent for user: {user_context.user_id}")

    # ===== 1. LLM =====
    llm = get_llm()
    grader_llm = get_llm(thinking=False)

    # ===== 2. Backend - Manager + Stable Proxy + CompositeBackend =====
    # Manager 负责用户级领取/恢复/重建；Proxy 保持 Agent 已持有的后端引用稳定。
    sandbox_manager_instance = None
    managed_sandbox = False
    claimed_sandbox = False
    manager_for_cleanup = None
    if enable_sandbox:
        try:
            from ..backends.sandbox_holder import set_sandbox
            from ..backends.sandbox_manager import sandbox_manager
            from ..backends.sandbox_proxy import SandboxBackendProxy

            manager_for_cleanup = sandbox_manager
            raw_sandbox = sandbox_manager.get_sandbox(user_context.user_id)
            claimed_sandbox = True
            sandbox_backend = SandboxBackendProxy(raw_sandbox)
            bootstrap_sandbox(sandbox_backend)
            sandbox_manager_instance = sandbox_manager
            managed_sandbox = True
            # 注册的是稳定 Proxy，而不是会在重建后失效的原始 Docker 连接。
            set_sandbox(sandbox_backend, user_context.user_id)
            agent_logger.info(
                f"Using managed Docker sandbox for {user_context.user_id}: "
                f"{sandbox_backend.container_name}"
            )
        except Exception as e:
            # bootstrap / 注册失败时，刚领取的容器不能留在缓存里变孤儿。
            if claimed_sandbox and manager_for_cleanup is not None:
                try:
                    manager_for_cleanup.destroy_user_sandbox(user_context.user_id)
                    agent_logger.info(
                        f"Destroyed unusable claimed sandbox for {user_context.user_id}"
                    )
                except Exception as cleanup_error:
                    agent_logger.warning(
                        f"Failed to clean up claimed sandbox for "
                        f"{user_context.user_id}: {cleanup_error}"
                    )
            agent_logger.warning(
                f"Managed Docker sandbox unavailable ({e}), falling back to LocalShell"
            )
            sandbox_backend = LocalShellBackend(virtual_mode=True)
            from ..backends.sandbox_holder import set_sandbox
            set_sandbox(None, user_context.user_id)
    else:
        # state 读取等非执行上下文：不领取容器，也不启动预热池。
        sandbox_backend = LocalShellBackend(virtual_mode=True)
        from ..backends.sandbox_holder import set_sandbox
        set_sandbox(None, user_context.user_id)
        agent_logger.info(
            f"Sandbox disabled for non-executing graph ({user_context.user_id})"
        )

    # deepagents >= 0.7 要求传入已初始化的 Backend 实例，
    # 不再接受旧版的 backend factory。
    backend_routes = {}
    if store is not None:
        backend_routes["/memories/"] = StoreBackend(
            namespace=lambda _rt: (user_context.user_id,),
        )
        backend_routes["/persisted-skills/"] = StoreBackend(
            namespace=lambda _rt: skills_store_namespace(user_context.user_id),
        )
    composite_backend = CompositeBackend(
        default=sandbox_backend,
        routes=backend_routes,
    )

    # ===== 3. 加载工具 =====
    from ..tools.chart_generator import generate_chart
    from ..tools.document_generator import generate_document, generate_table_report
    from ..tools.download_sandbox_file import download_sandbox_file, list_sandbox_files
    from ..tools.hitl_tools import request_order_info
    from ..tools.mcp_client import load_mcp_tools_sync
    from ..tools.skill_management import list_user_skills
    from ..tools.web_fetch import install_skill, web_fetch

    mcp_tools = load_mcp_tools_sync()
    custom_tools = [generate_chart, web_fetch, install_skill, list_user_skills, request_order_info,
                    download_sandbox_file, list_sandbox_files,
                    generate_document, generate_table_report]

    # 三层记忆：COLD 层按需检索 + 显式写入（WARM 层已在系统提示词里常驻注入）
    if DEFAULT_MEMORY_CONFIG.memory_tools_enabled:
        from ..tools.memory_tools import read_memory, remember
        custom_tools.extend([read_memory, remember])
    all_tools = mcp_tools + custom_tools

    # Prompt disclosure is independent from physical sandbox file synchronization.
    from ..skills import (
        SLOT,
        ProgressiveSkillsMiddleware,
        SkillCatalog,
        attach_to_subagents,
    )
    skill_catalog = SkillCatalog(SKILLS_DIR, store=store, user_id=user_context.user_id,
                                 sandbox=managed_sandbox)
    skill_read_tools = skill_catalog.tools()
    all_tools.extend(skill_read_tools)

    agent_logger.info(
        f"Tools loaded: {len(mcp_tools)} MCP + {len(custom_tools)} custom + "
        f"{len(skill_read_tools)} skill readers = {len(all_tools)} total"
    )

    # ===== 4. 加载子Agent配置 =====
    from ..subagents.loader import get_delegation_context_prompt, load_subagent_configs, resolve_subagent_tools
    subagent_configs = load_subagent_configs()
    subagents = resolve_subagent_tools(subagent_configs, all_tools)
    attach_to_subagents(subagents, skill_catalog, skill_read_tools)
    # 生成委派上下文协议（注入主 Agent 提示词）
    delegation_prompt = get_delegation_context_prompt(subagent_configs)

    # ===== 5. 组装中间件栈 =====
    # 自定义中间件
    # Harness 阶段状态机 + 评审器
    from ..middlewares.context_injection import ContextInjectionMiddleware
    from ..middlewares.memory_consolidation import MemoryConsolidationMiddleware
    from ..middlewares.memory_update import MemoryUpdateMiddleware
    from ..middlewares.order_intake import OrderIntakeMiddleware
    from ..middlewares.review_gate import ReviewExecutionGate, SafeRubricMiddleware
    from ..middlewares.runtime_binding import RuntimeBindingMiddleware
    from ..middlewares.sandbox_breaker import SandboxCircuitBreakerMiddleware
    from ..middlewares.sandbox_health import SandboxHealthMiddleware
    from ..middlewares.skills_sync import SkillsSyncMiddleware
    from ..middlewares.tools_summarization import ToolsSummarizationMiddleware
    from ..middlewares.user_skills_restore import UserSkillsRestoreMiddleware
    from ..middlewares.warm_memory import WARM_MEMORY_SLOT, WarmMemoryMiddleware
    from ..review import HarnessPhaseMiddleware, load_harness_config

    # 读取 Harness DSL 配置中的评审迭代上限
    _harness_config = load_harness_config()
    _review_cfg = _harness_config.get("review", {}) if isinstance(_harness_config, dict) else {}
    _review_max_iterations = _review_cfg.get("max_iterations", 3)
    from ..review import build_review_router
    router_llm = build_review_router(_review_cfg)

    # 注意：create_deep_agent 内部已自动添加：
    # - SummarizationMiddleware（自动摘要 + compact_conversation 工具）
    # - FilesystemMiddleware, SkillsMiddleware, SubAgentMiddleware
    # - TodoListMiddleware, PatchToolCallsMiddleware, MemoryMiddleware
    # 我们只需添加自定义中间件 + 调用限制
    # 沙箱重建后不仅替换底层 Docker 连接，还要重新填充项目、基础 Skills 和
    # 用户持久化 Skills。两个中间件持有同一个稳定 Proxy，缓存必须一起失效。
    skills_sync_middleware = SkillsSyncMiddleware(
        skills_dir=SKILLS_DIR,
        sandbox_backend=sandbox_backend if managed_sandbox else None,
    )
    user_skills_restore_middleware = UserSkillsRestoreMiddleware(
        store=store,
        user_id=user_context.user_id,
        sandbox_backend=sandbox_backend if managed_sandbox else None,
    )

    # bootstrap 刚把整个项目（含 backend/skills）拷进沙箱，这里直接认账，
    # 省掉首轮逐文件同步的 14 次 RPC；沙箱重建时仍会走全量回填。
    if managed_sandbox:
        skills_sync_middleware.assume_synced()

    def _rehydrate_after_sandbox_rebuild(_replacement) -> None:
        bootstrap_sandbox(sandbox_backend)
        skills_sync_middleware.invalidate()
        user_skills_restore_middleware.invalidate()
        # 立即回填，避免本次失败后的下一条请求才重新拥有 Skills。
        skills_sync_middleware.sync_now()
        user_skills_restore_middleware.restore_now()

    middlewares = [
        # --- 自定义中间件（执行顺序 1→7）---
        # 必须最外层：先绑定本 run 的 user/store/sandbox，内层工具和子 Agent 才能取到。
        RuntimeBindingMiddleware(
            user_id=user_context.user_id,
            store=store,
            sandbox=sandbox_backend if managed_sandbox else None,
        ),
        SandboxHealthMiddleware(
            sandbox_manager=sandbox_manager_instance,
            user_id=user_context.user_id,
            sandbox_backend=sandbox_backend if managed_sandbox else None,
            on_rebuild=_rehydrate_after_sandbox_rebuild if managed_sandbox else None,
            check_interval=SANDBOX_HEALTH_CHECK_INTERVAL_SECONDS,
        ),                                                            # 1. 沙箱健康检查 + 重建
        HarnessPhaseMiddleware(router_model=router_llm),               # 2. 混合路由 + rubric 注入
        ContextInjectionMiddleware(user_context=user_context),        # 3. 用户上下文注入
        OrderIntakeMiddleware(router_model=router_llm),               # 3.5 订单意图路由 + 缺字段确定性中断
        WarmMemoryMiddleware(store=store, user_id=user_context.user_id),
        ProgressiveSkillsMiddleware(skill_catalog),
        skills_sync_middleware,                                       # 4. 基础 Skills 同步
        user_skills_restore_middleware,                               # 5. 用户 Skills 恢复
        ToolsSummarizationMiddleware(),                               # 6. 摘要监控
        MemoryUpdateMiddleware(store=store, user_id=user_context.user_id),      # 7. 偏好提取（WARM）
        MemoryConsolidationMiddleware(store=store, user_id=user_context.user_id),   # 7.5 情节归档 + 遗忘（COLD）
        SandboxCircuitBreakerMiddleware(failure_threshold=3, recovery_timeout=60),  # 8. 熔断器
        # --- Harness 评审器（RubricMiddleware）---
        # 收到 rubric 后，grader 子Agent 结构化产出 satisfied/needs_revision/failed，
        # needs_revision 时自动打回模型重做，形成真实 Review 回路（非 prompt 软约束）
        SafeRubricMiddleware(model=grader_llm, max_iterations=_review_max_iterations),
        ReviewExecutionGate(_harness_config),  # after_agent 逆序：先升级，再审查
        # --- 框架内置中间件（调用限制）---
        ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS),          # 模型调用上限
        ToolCallLimitMiddleware(run_limit=MAX_TOOL_CALLS),            # 工具调用上限
    ]

    # ===== 6. 构建系统提示词 =====
    # 用逐项替换而不是 str.format：提示词里可能包含 LaTeX 花括号（如 \sqrt{d_k}），
    # str.format 会把 {d_k} 误当成占位符并抛 KeyError。
    system_prompt = MAIN_SYSTEM_PROMPT
    for _key, _val in (
        ("user_id", user_context.user_id),
        ("username", user_context.username),
        ("preferences", "以本次动态加载的记忆为准"),
        ("warm_memory", WARM_MEMORY_SLOT),
    ):
        system_prompt = system_prompt.replace("{" + _key + "}", str(_val))
    if DEFAULT_MEMORY_CONFIG.memory_tools_enabled:
        system_prompt += MEMORY_USAGE_PROMPT
    # 注入子Agent委派上下文协议
    if delegation_prompt:
        system_prompt += delegation_prompt

    # ===== 6.5 仅注入动态目录占位符，正文/资源由只读工具按需返回 =====
    system_prompt += "\n" + SLOT

    # ===== 7. 创建 Agent =====
    # 自定义渐进披露支持中文路径；不再把全部 SKILL.md 放入系统提示。
    # skills=None 防止框架与本项目双重加载；文件仍由原同步/恢复中间件准备。
    agent = create_deep_agent(
        model=llm,
        tools=all_tools,
        system_prompt=system_prompt,
        middleware=middlewares,
        subagents=subagents if subagents else None,
        skills=None,
        memory=[str(MEMORY_DIR / "AGENTS.md")],
        backend=composite_backend,
        interrupt_on=INTERRUPT_ON_TOOLS,
        checkpointer=checkpointer,
        store=store,
        name="procurement-main-agent",
    )

    agent_logger.info(
        f"Main agent created: {len(all_tools)} tools, "
        f"{len(subagents)} subagents, {len(middlewares)} middlewares"
    )
    return agent

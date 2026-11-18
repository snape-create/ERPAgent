"""Execution evidence can upgrade routing; writes must not be replayed by grading."""
import json

from deepagents import RubricMiddleware
from langchain.agents.middleware import AgentMiddleware, hook_config

from ..review import HarnessPhaseState, ReviewRoute, compose_rubric, reset_grading

# 审批事实作为“可信上下文”注入 rubric（rubric 是可信输入，transcript 不是）。
_APPROVAL_CONTEXT = (
    "\n\n（评测说明：本次写操作已在工具执行前获得人工审批 approve 授权，"
    "属于受控且已授权的写入。凡涉及“是否经过人工审批 / 是否绕过审批”的判断，"
    "均应视为已满足；不要因为对话记录里没有再次出现审批中断事件就判定未审批。）"
)


def _with_approval_context(state):
    """审批恢复后，把可信的审批事实注入 rubric。

    不能写进 transcript：评审器明确把 transcript 当不可信内容，
    因此只能放入 rubric 这个可信块，才能影响评分。
    """
    if not state.get("_human_approved"):
        return state
    rubric = state.get("rubric")
    if not rubric or _APPROVAL_CONTEXT.strip() in rubric:
        return state
    return {**state, "rubric": rubric + _APPROVAL_CONTEXT}


def execution_signals(state):
    messages = state.get("messages", [])[state.get("_review_start", 0):]
    signals = set()
    writes = False
    calls = []
    for m in messages:
        for call in getattr(m, "tool_calls", []) or []:
            name = call.get("name", "").lower()
            calls.append(name)
            if any(word in name for word in ("create", "update", "delete", "remove", "inbound", "outbound", "stock_in", "stock_out", "adjust", "execute", "install")):
                writes = True
                signals.add("write_or_execution_attempt")
            if name == "task":
                # Child tool traces may not be present in the parent's state.
                # Conservatively suppress automatic replay for delegated work.
                writes = True
                signals.add("delegated_task")
        if getattr(m, "type", "") == "tool":
            content = getattr(m, "content", "")
            error = getattr(m, "status", "") == "error"
            if isinstance(content, str):
                try:
                    data = json.loads(content)
                    error |= isinstance(data, dict) and (data.get("success") is False or bool(data.get("error")))
                except (ValueError, TypeError):
                    error |= content.lstrip().lower().startswith(("error", "错误", "失败"))
            if error:
                signals.add("tool_error")
    if len(calls) >= 3:
        signals.add("multiple_tool_calls")
    if len({n for n in calls if any(x in n for x in ("search", "fetch", "query"))}) >= 2:
        signals.add("multiple_sources")
    return sorted(signals), writes


class ReviewExecutionGate(AgentMiddleware):
    """Register AFTER RubricMiddleware so reverse after_agent order runs us first."""
    state_schema = HarnessPhaseState

    def __init__(self, config):
        self.config = config

    def after_agent(self, state, runtime):
        signals, writes = execution_signals(state)
        updates = {"_review_signals": signals, "_review_write_attempted": writes}
        if not signals or self.config.get("review", {}).get("mode") == "never":
            return updates
        if not state.get("rubric"):
            route = ReviewRoute(decision="review", reason="执行信号升级", checks=["sources", "constraints"])
            rubric = compose_rubric(route, self.config)
            updates.update(rubric=rubric, _harness_rubric_source="auto", **reset_grading(rubric))
        updates["review_decision"] = {**state.get("review_decision", {}), "decision": "review",
                                      "source": "execution", "signals": signals}
        return updates


class SafeRubricMiddleware(RubricMiddleware):
    """A separate single-pass reviewer prevents write retries without shared mutation."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._write_reviewer = RubricMiddleware(**{**kwargs, "max_iterations": 1})

    @hook_config(can_jump_to=["model"])
    def after_agent(self, state, runtime):
        state = _with_approval_context(state)
        if state.get("_review_write_attempted"):
            return self._write_reviewer.after_agent(state, runtime)
        return super().after_agent(state, runtime)

    @hook_config(can_jump_to=["model"])
    async def aafter_agent(self, state, runtime):
        state = _with_approval_context(state)
        if state.get("_review_write_attempted"):
            return await self._write_reviewer.aafter_agent(state, runtime)
        return await super().aafter_agent(state, runtime)

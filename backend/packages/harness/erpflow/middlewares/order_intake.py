"""订单信息采集中间件。

把「缺字段就弹表单」从中断工具（hitl_tools.request_order_info）搬到
模型调用之前，触发条件完全由本模块的代码判定：

    用户消息命中「创建订单」意图
      → before_agent 建一份空草稿（能正则抽到的字段先填上）
      → before_model 用 validate_order_data 算缺口
      → 有缺口就 interrupt（形状与 request_order_info 一致，前端无需改动）
      → 用户提交表单后合并进草稿
      → 字段齐了才放行给模型，由模型调用 order_create 触发审批卡

这样「弹不弹表单」不再取决于模型是否愿意调用某个工具。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, cast

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
    Runtime,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from ..log_utils import middleware_logger
from ..tools.hitl_tools import parse_supplement_text, validate_order_data


class OrderIntakeState(AgentState):
    """在 AgentState 上追加订单采集相关的三个字段。"""

    order_intake: NotRequired[bool]
    """本轮是否处于「创建订单」流程。"""

    order_draft: NotRequired[dict[str, Any]]
    """已收集到的订单草稿，结构同 order_create 的 order_data。"""

    order_intake_source: NotRequired[str]
    """触发本次采集的用户消息 id，用于区分「同一轮」和「新一轮」。"""


# 知识类疑问排除；“能不能/可以帮我/…吗”属于礼貌请求，不排除
_QUESTION = re.compile(r"怎么|如何|流程|是什么|介绍|说明|步骤|原理|区别")
_CREATE_ORDER = re.compile(
    r"(?:新增|创建|新建|开)\s*(?:一|1)?\s*[个张笔]?\s*(?:采购)?订单"
    r"|(?:采购)?订单\s*(?:创建|新增|新建)"
    r"|下\s*(?:一|1)?\s*[个张笔]?\s*单"
)


def is_create_order_intent(text: str) -> bool:
    """规则兜底：只在路由模型不可用/超时时使用。"""
    if not text or _QUESTION.search(text):
        return False
    return bool(_CREATE_ORDER.search(text))


class OrderIntentDecision(BaseModel):
    create_order: bool = Field(description="用户是否明确要求新建一张采购订单")
    reason: str = Field(default="", description="一句话理由")


_INTENT_PROMPT = """判断用户最后一条消息是否在要求「新建/创建一张采购订单」，只输出结构化结果。

判 true：明确要求下单、创建、新增、新建订单；或“下个单”“再来一单”“再下两个”这类承接上下文的指代。
判 false：查询/修改/删除订单、询问流程或概念、闲聊、与订单无关。

结合最近对话理解指代；不要因为句子长或语气客气就判 true。"""


def _text_of(message: BaseMessage) -> str:
    content = getattr(message, "content", "") or ""
    if isinstance(content, list):
        content = " ".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict)
        )
    return str(content)


def _intent_messages(state: OrderIntakeState) -> list[BaseMessage]:
    """给路由模型的输入：最近几条对话 + 分类指令（跳过工具消息）。"""
    lines: list[str] = []
    for message in (state.get("messages") or [])[-6:]:
        kind = getattr(message, "type", "")
        if kind not in ("human", "ai"):
            continue
        content = _text_of(message)[:300].strip()
        if content:
            lines.append(f"{'用户' if kind == 'human' else '助手'}: {content}")
    return [SystemMessage(content=_INTENT_PROMPT), HumanMessage(content="\n".join(lines))]


def _seed_draft(text: str) -> dict[str, Any]:
    """能直接从用户消息里抽到的字段先填上，抽不到就留空。"""
    try:
        return parse_supplement_text(text, {})
    except Exception:
        return {}


def _merge_form(
    draft: dict[str, Any], form: dict[str, Any] | str | None
) -> dict[str, Any]:
    """把中断返回的表单数据合并进草稿。

    前端提交的是整张表单的 JSON（不是增量），所以直接 update 即可；
    自由文本（旧协议）回退到 parse_supplement_text。
    """
    text = form.get("supplement") if isinstance(form, dict) else form
    merged = dict(draft)
    # parse_supplement_text 会对已存在的空列表取 [0]，这里先补一条空明细
    if not merged.get("orderDetail"):
        merged["orderDetail"] = [{}]

    if isinstance(text, str):
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            merged.update(parsed)
            if not merged.get("orderDetail"):
                merged["orderDetail"] = [{}]
            return merged
        try:
            return parse_supplement_text(text, merged)
        except Exception:
            middleware_logger.warning("Order intake: failed to parse supplement text")
    return merged


class OrderIntakeMiddleware(AgentMiddleware[OrderIntakeState, None, Any]):
    """订单采集：意图交给路由模型，缺口与中断交给代码。"""

    state_schema = OrderIntakeState

    def __init__(
        self, router_model: BaseChatModel | None = None, timeout: float = 8.0
    ) -> None:
        self._router = router_model
        self._timeout = timeout

    # ---------- 一次性入口：判断本轮是不是创建订单 ----------
    def before_agent(
        self, state: OrderIntakeState, runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        gate = self._gate(state)
        if gate is None:
            return None
        source, text = gate
        if state.get("order_intake_source") == source:
            return None  # 同一轮（含表单 resume），不再调路由模型
        return self._enter(text, source, self._decide_sync(state))

    async def abefore_agent(
        self, state: OrderIntakeState, runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        gate = self._gate(state)
        if gate is None:
            return None
        source, text = gate
        if state.get("order_intake_source") == source:
            return None
        return self._enter(text, source, await self._adecide(state))

    # ---------- 意图判定：模型优先，规则兜底 ----------
    def _decide_sync(self, state: OrderIntakeState) -> bool:
        text = self._last_text(state)
        if self._router is None:
            return is_create_order_intent(text)
        try:
            verdict = self._router.with_structured_output(OrderIntentDecision).invoke(
                _intent_messages(state)
            )
            return bool(getattr(verdict, "create_order", False))
        except Exception as exc:
            middleware_logger.warning(f"Order intent router fallback to rule: {exc}")
            return is_create_order_intent(text)

    async def _adecide(self, state: OrderIntakeState) -> bool:
        text = self._last_text(state)
        if self._router is None:
            return is_create_order_intent(text)
        try:
            verdict = await asyncio.wait_for(
                self._router.with_structured_output(OrderIntentDecision).ainvoke(
                    _intent_messages(state)
                ),
                timeout=self._timeout,
            )
            return bool(getattr(verdict, "create_order", False))
        except Exception as exc:
            middleware_logger.warning(f"Order intent router fallback to rule: {exc}")
            return is_create_order_intent(text)

    # ---------- 状态流转 ----------
    def _gate(self, state: OrderIntakeState) -> tuple[str, str] | None:
        """返回 (消息 id, 文本)；没有用户消息时返回 None。"""
        messages = state.get("messages") or []
        last_human = next(
            (m for m in reversed(messages) if isinstance(m, HumanMessage)), None
        )
        if last_human is None:
            return None
        source = getattr(last_human, "id", None) or f"len-{len(messages)}"
        return source, _text_of(last_human)

    def _last_text(self, state: OrderIntakeState) -> str:
        gate = self._gate(state)
        return gate[1] if gate else ""

    def _enter(self, text: str, source: str, is_order: bool) -> dict[str, Any]:
        if not is_order:
            return {
                "order_intake": False,
                "order_draft": {},
                "order_intake_source": source,
            }
        draft = _seed_draft(text)
        middleware_logger.info(
            f"Order intake started: missing={validate_order_data(draft)}"
        )
        return {
            "order_intake": True,
            "order_draft": draft,
            "order_intake_source": source,
        }

    # ---------- 模型调用之前：代码判定缺口并中断 ----------
    def before_model(
        self, state: OrderIntakeState, runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        return self._check(state)

    async def abefore_model(
        self, state: OrderIntakeState, runtime: Runtime[None]
    ) -> dict[str, Any] | None:
        return self._check(state)

    def _check(self, state: OrderIntakeState) -> dict[str, Any] | None:
        if not state.get("order_intake"):
            return None

        draft = state.get("order_draft") or {}
        missing = validate_order_data(draft)
        if not missing:
            return None

        message = f"请补充以下订单信息: {', '.join(missing)}"
        details = draft.get("orderDetail") or []
        if any("partId" in item for item in missing) and any(
            isinstance(line, dict) and line.get("partCode") and not line.get("partId")
            for line in details
        ):
            message += "（零件编码未匹配到 partId，请改填数字零件 ID）"

        form = interrupt({
            "type": "order_info_request",
            "missing_fields": missing,
            "current_data": draft,
            "message": message,
        })
        # 一次把字段问全；提交后若仍缺，会再走一次本函数（中断点仍在调模型之前）
        return {"order_draft": _merge_form(draft, form)}

    # ---------- 字段齐了：把草稿注入模型上下文 ----------
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelCallResult[Any]:
        return handler(self._inject(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelCallResult[Any]:
        return await handler(self._inject(request))

    def _inject(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        state = cast(
            OrderIntakeState,
            request.state if isinstance(request.state, dict) else {},
        )
        draft = state.get("order_draft")
        if not state.get("order_intake") or not draft:
            return request

        note = (
            "\n\n用户已通过表单确认以下订单，字段齐全，可以直接提交：\n"
            f"{json.dumps(draft, ensure_ascii=False)}\n"
            "直接调用 order_create，order_data 参数填这段 JSON 字符串；"
            "不要再向用户索要任何字段，也不要在回复里复述这段 JSON；"
            "创建前先用表格展示一次订单摘要。"
        )
        # system_message.content 可能是 str，也可能被其它中间件改成了
        # content blocks（[{"type": "text", "text": ...}]）；统一取文本再拼接，
        # 避免 list + str 直接抛 TypeError。
        message = request.system_message
        base = ""
        if message is not None:
            content = message.content
            if isinstance(content, str):
                base = content
            elif isinstance(content, list):
                base = "\n".join(
                    str(block.get("text", ""))
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                )
            else:
                base = str(content)

        return dataclasses.replace(
            request, system_message=SystemMessage(content=base + note)
        )

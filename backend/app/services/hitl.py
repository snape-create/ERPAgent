"""HITL（Human-in-the-loop）载荷整形：中断 → 前端可渲染卡片。

两件事：
1. 前端发来的 edit 决策要翻译成 LangGraph 要求的 ``edited_action`` 形状，
   且 order_* 工具参数必须重新包回 ``{"order_data": "<json>"}``；
2. LangGraph 抛出的中断值要拍平成审批卡片/补全表单的字段。

ERP 事实查询（零件编码映射、当前订单状态）在
``erpflow.integrations.erp.approval``，这里只做形状转换。
"""
import json

from erpflow.integrations.erp.approval import fetch_order
from erpflow.integrations.erp.status import status_name
from erpflow.log_utils import web_logger

# 这俩工具在 MCP 侧签名是 order_data: str，散字段必须重新包成 JSON 字符串
ORDER_TOOLS_WITH_JSON_ARGS = ("order_create", "order_update")


def unwrap_order_args(args: dict) -> dict:
    """把 ``{"order_data": "<json 字符串>"}`` 摊平成单据字段。

    不拆开的话审批卡片只能拿到一个字符串字段，既画不出明细表，
    也判断不出这是订单类写操作。解析失败时原样返回，卡片退化成键值表。
    """
    raw = args.get("order_data")
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return args
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return args
    if not isinstance(parsed, dict):
        return args
    # order_update 的 args 还带 id，保留其它键，单据字段覆盖到顶层
    merged = {key: value for key, value in args.items() if key != "order_data"}
    merged.update(parsed)
    return merged


def normalize_edit_decisions(decisions: list) -> list:
    """把前端「改完参数再提交」的 edit 决策翻译成框架要求的形状。

    前端只发 ``{"type": "edit", "tool_name": ..., "args": {...}}``；
    LangGraph 的 HumanInTheLoop 要求 ``edited_action`` 包裹。
    """
    normalized = []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        if decision.get("type") != "edit" or "edited_action" in decision:
            normalized.append(decision)
            continue

        tool_name = decision.get("tool_name") or ""
        args = decision.get("args")
        if not isinstance(args, dict):
            args = {}
        if tool_name in ORDER_TOOLS_WITH_JSON_ARGS:
            args = {"order_data": json.dumps(args, ensure_ascii=False)}
        normalized.append({
            "type": "edit",
            "edited_action": {"name": tool_name, "args": args},
        })
    return normalized


def is_approved(decisions: list) -> bool:
    """approve 与 edit 都表示人已确认，只是后者提交前改了参数。"""
    return any(
        isinstance(d, dict) and d.get("type") in ("approve", "edit")
        for d in decisions
    )


def flatten_hitl_actions(interrupt_value: dict) -> dict:
    """把 HITL 中断的 action_requests 拍平成前端可直接渲染的结构。

    LangGraph 的 action_requests 形如
    ``[{"name": "order_create", "args": {...}, "description": "..."}]``，
    整体塞给前端会让 Object.entries() 只能拿到数组下标，卡片渲染不出单据内容。
    这里取第一条动作把 args 提到顶层；一次中断含多个动作时保留数量供前端提示。
    """
    raw = interrupt_value.get("action_requests")
    if isinstance(raw, dict):
        actions = [raw]
    elif isinstance(raw, list):
        actions = [item for item in raw if isinstance(item, dict)]
    else:
        actions = []

    first = actions[0] if actions else {}
    args = first.get("args")
    if not isinstance(args, dict):
        args = {}
    args = unwrap_order_args(args)
    return {
        "tool_name": first.get("name") or interrupt_value.get("tool_name", ""),
        "tool_description": first.get("description", ""),
        "tool_args": args,
        "order_data": args,
        "action_count": len(actions),
    }


def build_interrupt_event(interrupt_value: dict) -> dict:
    """把 LangGraph 的中断值转成前端可渲染的 payload。

    SSE 流与 ``GET /{thread_id}/state`` 共用：刷新页面后靠它把待审批卡片补回来。
    """
    if interrupt_value.get("type") == "order_info_request":
        return {
            "interrupt_type": "order_info_supplement",
            "missing_fields": interrupt_value.get("missing_fields", []),
            "message": interrupt_value.get("message", ""),
            "extracted_data": interrupt_value.get("current_data", {}),
        }
    if interrupt_value.get("action_requests"):
        return {
            "interrupt_type": "hitl_approval",
            **flatten_hitl_actions(interrupt_value),
        }
    return {
        "interrupt_type": "hitl_approval",
        "tool_name": interrupt_value.get("tool_name", ""),
        "tool_args": interrupt_value.get("tool_args", interrupt_value),
        "order_data": interrupt_value.get("tool_args", interrupt_value),
    }


async def enrich_status_change(payload: dict) -> dict:
    """状态变更的审批卡片需要「当前 → 目标」，否则用户看不出是前进还是回退。

    只对 order_update_status 生效。读取失败时保持原样，前端会降级成只显示目标状态。
    """
    if payload.get("tool_name") != "order_update_status":
        return payload

    args = payload.get("tool_args") or {}
    order_id = args.get("id")
    if order_id is None:
        return payload

    order = await fetch_order(order_id)
    if not order or order.get("status") is None:
        return payload

    try:
        payload["order_number"] = order.get("orderNumber")
        payload["current_status"] = order.get("status")
        payload["current_status_label"] = status_name(order.get("status"))
        payload["target_status_label"] = status_name(args.get("status"))
    except Exception as exc:  # 兜底：卡片不能因为文案生成失败而整体 500
        web_logger.warning(f"Failed to enrich status change interrupt: {exc}")
    return payload

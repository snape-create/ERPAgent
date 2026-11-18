"""审批 / 补充信息流程需要的 ERP 事实查询。

Web 层只负责把结构化结果画成卡片，不再直接拼 ERP 请求：
- ``resolve_part_codes``：补充信息里用零件编码填明细时，翻译成订单接口要的 partId
- ``fetch_order``：状态变更审批需要「当前状态」，用于展示 当前 → 目标
"""
import json

from ...log_utils import agent_logger
from .client import erp_client

# 零件编码 → ID 的映射表规模：一次拉一页足够，查不到就交给工具提示缺字段
PARTS_PAGE_SIZE = 200


def _parse_object(raw: str) -> dict | None:
    """把 JSON 字符串解析成 dict；任何异常都返回 None（调用方原样返回）。"""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


async def resolve_part_codes(supplement: str) -> str:
    """补充信息里用零件编码（P-001）代替数字 ID 时，先翻译成 partId。

    表单允许填编码，但订单接口要的是 parts.id。查一次零件列表做映射；
    查不到就原样返回，交给工具按缺字段提示，不在这里猜。
    """
    data = _parse_object(supplement)
    if data is None:
        return supplement

    details = data.get("orderDetail")
    if not isinstance(details, list):
        return supplement

    pending = [
        item for item in details
        if isinstance(item, dict) and not item.get("partId") and item.get("partCode")
    ]
    if not pending:
        return supplement

    try:
        page = await erp_client.get(
            "/api/parts/page", params={"current": 1, "size": PARTS_PAGE_SIZE}
        )
        records = page.get("records") if isinstance(page, dict) else None
        mapping = {
            str(row.get("partCode", "")).upper(): row.get("id")
            for row in (records or [])
        }
        for item in pending:
            code = str(item.get("partCode", "")).strip().upper()
            if mapping.get(code):
                item["partId"] = mapping[code]
            item.pop("partCode", None)
    except Exception as exc:
        agent_logger.warning(f"Resolve partCode failed: {exc}")
        return supplement

    return json.dumps(data, ensure_ascii=False)


async def fetch_order(order_id) -> dict | None:
    """按 ID 查询订单；失败返回 None（审批卡片会降级成只显示目标状态）。"""
    try:
        current = await erp_client.get(f"/api/orders/get/{order_id}")
    except Exception as exc:
        agent_logger.warning(f"Failed to fetch order {order_id}: {exc}")
        return None
    order = current.get("data") if isinstance(current, dict) and isinstance(current.get("data"), dict) else current
    return order if isinstance(order, dict) else None

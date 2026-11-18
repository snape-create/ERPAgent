"""采购订单状态字典 —— 全项目唯一事实源。

MCP 工具（order_tools）、审批卡片、Supplement 流程都从这里取，禁止各自再抄一份。
"""

# 0=待审核, 1=已审核, 2=已发货, 3=已收货, 4=已完成
ORDER_STATUS_NAMES: dict[int, str] = {
    0: "待审核",
    1: "已审核",
    2: "已发货",
    3: "已收货",
    4: "已完成",
}

# 只允许按流程前进一格；不允许跳跃，也不允许回退
ORDER_STATUS_FLOW: dict[int, int] = {0: 1, 1: 2, 2: 3, 3: 4}


def status_name(status) -> str:
    """状态码 → 中文名；未知状态保留原始值便于排查。"""
    return ORDER_STATUS_NAMES.get(status, f"未知状态({status})")

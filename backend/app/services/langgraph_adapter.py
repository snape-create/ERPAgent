"""LangGraph SDK ``StreamPart`` → ``ChatTurn`` 输入的适配层。

W1b 只准备适配能力，不切换生产流量：网关仍走进程内 ``session.agent.astream``，
拿到的是 ``(namespace, mode, chunk)``。切到 ``langgraph_sdk.runs.stream`` 后有三处差异：

1. namespace 被拼进事件名（``"messages|analyst:1"``），主图没有后缀；
2. 消息是 JSON 序列化后的 dict，不再是 ``AIMessageChunk`` / ``ToolMessage`` 对象；
3. 中断项被包成 ``{"value": ..., "id": ...}``。

本模块把这三处差异抹平，让 ``ChatTurn`` 的事件名 / 顺序 / 字段保持不变。
"""
# 控制类事件：不产生业务事件。error 由 chat_stream 的现有异常分支统一封装
IGNORED_MODES = frozenset({"metadata", "end", "error"})
# data 原样透传的模式
PASSTHROUGH_MODES = frozenset({"values", "custom"})
# data 是 [message, metadata] 元组的模式（messages-tuple 只是请求侧别名）
MESSAGE_MODES = frozenset({"messages", "messages-tuple"})

_MISSING = object()


def split_event(event: str) -> tuple[str, list[str]]:
    """拆分事件名：``"values|analyst:1"`` → ``("values", ["analyst:1"])``。

    主图没有后缀，namespace 为 ``[]``；空片段直接丢弃，避免把 ``"values|"``
    这种残缺事件名误判成子图。
    """
    mode, *namespace = (event or "").split("|")
    return mode, [part for part in namespace if part]


def adapt_stream_part(event: str, data):
    """把一条 SDK ``StreamPart`` 适配成 ``(namespace, mode, payload)``。

    返回 ``None`` 表示这条事件不该进入 ``ChatTurn``：``metadata`` / ``end`` /
    ``error`` 以及当前不消费的模式（``updates`` / ``tasks`` / ``debug`` 等）。
    """
    mode, namespace = split_event(event)
    if mode in IGNORED_MODES:
        return None
    if mode in PASSTHROUGH_MODES:
        return namespace, mode, data
    if mode in MESSAGE_MODES:
        return namespace, mode, _message_payload(data)
    return None


def _message_payload(data) -> list:
    """``messages`` 的 data 统一成 ``[message, metadata]``，保持现有下标访问。"""
    if isinstance(data, (list, tuple)):
        if len(data) >= 2:
            return [data[0], data[1]]
        if len(data) == 1:
            return [data[0], {}]
        return [None, {}]
    return [data, {}]


def message_field(message, key, default=None):
    """按字段名读取消息：``BaseMessage`` 走属性，SDK 序列化后的 dict 走键。"""
    if isinstance(message, dict):
        return message.get(key, default)
    if message is None:
        return default
    return getattr(message, key, default)


def unwrap_interrupt(item):
    """取出中断项里的真正中断值。

    SDK 形状是 ``{"value": x, "id": y}`` → ``x``；进程内形状是带 ``value``
    属性的对象，同样取出 ``.value``；已经裸的中断值原样返回。
    """
    if isinstance(item, dict):
        return item["value"] if "value" in item else item
    value = getattr(item, "value", _MISSING)
    return item if value is _MISSING else value

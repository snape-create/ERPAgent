"""SDK StreamPart 适配层与 ChatTurn dict 兼容的单测。

不连 Docker / Mongo / 模型：流数据用 SDK 实际形状（dict 消息、带 namespace 的
事件名、``{"value", "id"}`` 中断项）构造，只验证事件名、顺序与关键字段。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from app.services import chat_turn
from app.services.chat_turn import ChatTurn
from app.services.langgraph_adapter import (
    adapt_stream_part,
    message_field,
    split_event,
    unwrap_interrupt,
)


class AdaptStreamPartTests(unittest.TestCase):
    def test_namespace_main_and_subgraph(self):
        main = adapt_stream_part("messages", [{"type": "ai"}, {}])
        self.assertEqual(main[0], [])
        self.assertEqual(main[1], "messages")

        sub = adapt_stream_part("messages|analyst:1|researcher:2", [{"type": "ai"}, {}])
        self.assertEqual(sub[0], ["analyst:1", "researcher:2"])

        values = adapt_stream_part("values|agent:abc", {"phase": "executing"})
        self.assertEqual(values[0], ["agent:abc"])
        self.assertEqual(values[1], "values")

    def test_split_event_drops_empty_segments(self):
        self.assertEqual(split_event("values"), ("values", []))
        self.assertEqual(split_event("values|"), ("values", []))
        self.assertEqual(split_event("custom|analyst:1"), ("custom", ["analyst:1"]))

    def test_values_and_custom_passthrough(self):
        state = {"phase": "executing", "todos": [{"content": "查库存", "status": "pending"}]}
        namespace, mode, payload = adapt_stream_part("values", state)
        self.assertEqual(namespace, [])
        self.assertEqual(mode, "values")
        self.assertIs(payload, state)

        custom = {"type": "rubric_evaluation_end", "result": "satisfied"}
        namespace, mode, payload = adapt_stream_part("custom|analyst:1", custom)
        self.assertEqual(namespace, ["analyst:1"])
        self.assertEqual(mode, "custom")
        self.assertIs(payload, custom)

    def test_control_events_ignored(self):
        self.assertIsNone(adapt_stream_part("metadata", {"run_id": "r1"}))
        self.assertIsNone(adapt_stream_part("end", {}))
        self.assertIsNone(adapt_stream_part("error", {"message": "boom"}))

    def test_message_payload_keeps_tuple_shape(self):
        message = {"type": "ai", "content": "你好"}
        metadata = {"tags": ["review_router"]}

        namespace, mode, payload = adapt_stream_part(
            "messages-tuple|analyst:1", [message, metadata]
        )
        self.assertEqual(namespace, ["analyst:1"])
        self.assertEqual(mode, "messages-tuple")
        self.assertEqual(payload, [message, metadata])
        self.assertIs(payload[0], message)
        self.assertIs(payload[1], metadata)

        # 服务端只给一条消息时补空 metadata，保持 chunk[1] 可取
        _, _, payload = adapt_stream_part("messages", [message])
        self.assertEqual(payload, [message, {}])

    def test_message_field_reads_object_and_dict(self):
        ai = AIMessage(content="对象", name="rubric_grader")
        self.assertEqual(message_field(ai, "type"), "ai")
        self.assertEqual(message_field(ai, "content"), "对象")
        self.assertEqual(message_field(ai, "name"), "rubric_grader")
        self.assertIsNone(message_field(ai, "tool_call_id"))

        tool = ToolMessage(content="结果", tool_call_id="call-1", name="query_stock")
        self.assertEqual(message_field(tool, "type"), "tool")
        self.assertEqual(message_field(tool, "tool_call_id"), "call-1")

        data = {"type": "tool", "content": "结果", "tool_call_id": "call-1"}
        self.assertEqual(message_field(data, "type"), "tool")
        self.assertEqual(message_field(data, "tool_call_id"), "call-1")
        self.assertEqual(message_field(data, "name", "unknown"), "unknown")
        self.assertIsNone(message_field(None, "type"))

    def test_unwrap_interrupt(self):
        value = {"action_requests": [{"name": "order_create", "args": {}}]}
        self.assertIs(unwrap_interrupt({"value": value, "id": "int-1"}), value)
        # 已是裸中断值：dict 里没有 value 键，原样返回
        self.assertIs(unwrap_interrupt(value), value)
        # 进程内路径给的是带 value 属性的对象
        self.assertIs(unwrap_interrupt(SimpleNamespace(value=value)), value)


class ChatTurnDictStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patch = patch.object(chat_turn, "save_display_messages", AsyncMock())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def turn(self):
        return ChatTurn("thread-1", [])

    async def test_dict_message_emits_token_with_source(self):
        turn = self.turn()
        events = await turn.handle_message(
            ["analyst:1"], [{"type": "ai", "content": "库存充足"}, {"tags": []}]
        )
        self.assertEqual([name for name, _ in events], ["thinking", "token"])
        self.assertEqual(events[1][1]["content"], "库存充足")
        self.assertEqual(events[1][1]["source"], "analyst")

        # 主图文本不带 namespace 后缀
        main_events = await turn.handle_message([], [{"type": "ai", "content": "好的"}, {}])
        self.assertEqual(main_events[-1][1]["source"], "main")

    async def test_dict_message_emits_tool_events(self):
        turn = self.turn()
        chunks = [
            {"name": "query_stock", "args": "{\"part", "id": "call-1"},
            {"name": None, "args": "_no\": 1}", "id": None},
        ]
        events = await turn.handle_message(
            [], [{"type": "ai", "content": "", "tool_call_chunks": chunks}, {"tags": []}]
        )
        self.assertEqual(
            [name for name, _ in events],
            ["thinking", "tool_start", "tool_args", "tool_args"],
        )
        self.assertEqual(events[1][1], {"name": "query_stock", "id": "call-1"})
        self.assertEqual(turn.tool_calls[0]["args"], "{\"part_no\": 1}")

        result_events = await turn.handle_message(
            [],
            [
                {
                    "type": "tool",
                    "name": "query_stock",
                    "tool_call_id": "call-1",
                    "content": "库存 3 件",
                },
                {"tags": []},
            ],
        )
        self.assertEqual([name for name, _ in result_events], ["tool_result", "tool_end"])
        self.assertEqual(result_events[0][1]["name"], "query_stock")
        self.assertEqual(result_events[0][1]["id"], "call-1")
        self.assertEqual(result_events[0][1]["content"], "库存 3 件")
        self.assertEqual(result_events[1][1], {"id": "call-1"})
        self.assertEqual(turn.tool_calls[0]["status"], "done")
        self.assertEqual(turn.tool_calls[0]["result"], "库存 3 件")

    async def test_review_router_tags_filtered(self):
        turn = self.turn()
        events = await turn.handle_message(
            [],
            [
                {"type": "ai", "content": "internal route decision"},
                {"tags": ["review_router"]},
            ],
        )
        self.assertEqual(events, [])

    async def test_review_router_continuations_filtered_without_ids(self):
        """E1：首块带 tags + name，后续 name=None 只有 args，且 id 全部缺失。"""
        turn = self.turn()
        first = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "content": "",
                    "tool_call_chunks": [
                        {"name": "ReviewRoute", "args": "", "id": None},
                    ],
                },
                {"tags": ["review_router"]},
            ],
        )
        self.assertEqual(first, [])

        collected = []
        for args in ('{"', "deci", 'sion": "review"}'):
            collected.extend(await turn.handle_message(
                [],
                [
                    {
                        "type": "ai",
                        "content": "",
                        "tool_call_chunks": [
                            {"name": None, "args": args, "id": None},
                        ],
                    },
                    {"tags": []},
                ],
            ))
        # 内部消息后续的文本片段同样不得作为正文
        collected.extend(await turn.handle_message(
            [],
            [{"type": "ai", "content": "internal reasoning"}, {"tags": []}],
        ))

        self.assertEqual([name for name, _ in collected if name in ("tool_args", "token")], [])
        self.assertEqual(turn.tool_calls, [])

    async def test_review_router_continuations_filtered_by_call_id(self):
        """E1：id 只在首块出现时，后续片段按 tool_call id 继续丢弃。"""
        turn = self.turn()
        first = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "content": "",
                    "tool_call_chunks": [
                        {"name": "ReviewRoute", "args": "", "id": "call-route"},
                    ],
                },
                {"tags": ["review_router"]},
            ],
        )
        self.assertEqual(first, [])

        rest = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "content": "",
                    "tool_call_chunks": [
                        {"name": None, "args": '{"decision": "review"}', "id": "call-route"},
                        {"name": None, "args": '{"more": 1}', "id": None},
                    ],
                },
                {"tags": []},
            ],
        )
        self.assertEqual([name for name, _ in rest if name == "tool_args"], [])
        self.assertEqual(turn.tool_calls, [])

    async def test_business_tool_sequence_unchanged(self):
        """E2：正常业务工具仍按 tool_start → tool_args → tool_result → tool_end 输出。"""
        turn = self.turn()
        events = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "content": "",
                    "tool_call_chunks": [
                        {"name": "query_stock", "args": '{"part', "id": "call-1"},
                        {"name": None, "args": '_no": 1}', "id": None},
                    ],
                },
                {"tags": []},
            ],
        )
        self.assertEqual(
            [name for name, _ in events],
            ["thinking", "tool_start", "tool_args", "tool_args"],
        )

        result = await turn.handle_message(
            [],
            [
                {
                    "type": "tool",
                    "name": "query_stock",
                    "tool_call_id": "call-1",
                    "content": "库存 3 件",
                },
                {"tags": []},
            ],
        )
        self.assertEqual([name for name, _ in result], ["tool_result", "tool_end"])
        self.assertEqual(turn.tool_calls[0]["args"], '{"part_no": 1}')

    async def test_internal_and_business_calls_in_same_turn(self):
        """E3：同一轮里评审调用与业务工具调用互不干扰。"""
        turn = self.turn()
        # 评审路由器：首块带标签，后续只有 args
        await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "id": "msg-router",
                    "content": "",
                    "tool_call_chunks": [{"name": "ReviewRoute", "args": "", "id": "call-route"}],
                },
                {"tags": ["review_router"]},
            ],
        )
        leaked = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "id": "msg-router",
                    "content": "",
                    "tool_call_chunks": [
                        {"name": None, "args": '{"decision": "review"}', "id": "call-route"},
                    ],
                },
                {"tags": []},
            ],
        )
        self.assertEqual(leaked, [])

        business = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "id": "msg-main",
                    "content": "",
                    "tool_call_chunks": [
                        {"name": "query_stock", "args": '{"part_no": 1}', "id": "call-1"},
                    ],
                },
                {"tags": []},
            ],
        )
        self.assertEqual(
            [name for name, _ in business],
            ["thinking", "tool_start", "tool_args"],
        )

        result = await turn.handle_message(
            [],
            [
                {"type": "tool", "name": "query_stock", "tool_call_id": "call-1", "content": "3"},
                {"tags": []},
            ],
        )
        self.assertEqual([name for name, _ in result], ["tool_result", "tool_end"])

        text = await turn.handle_message(
            [], [{"type": "ai", "id": "msg-main", "content": "库存充足"}, {"tags": []}]
        )
        self.assertEqual([name for name, _ in text], ["token"])
        self.assertEqual(text[0][1]["content"], "库存充足")
        self.assertEqual([call["name"] for call in turn.tool_calls], ["query_stock"])

    async def test_interrupt_value_wrapper_restores_card(self):
        turn = self.turn()
        actions = [
            {
                "name": "order_create",
                "args": {"orderNumber": "PO1", "orderDetail": [{"partId": 1, "quantity": 2}]},
                "description": "创建采购订单",
            }
        ]
        events, interrupted = await turn.handle_values(
            [],
            {
                "__interrupt__": [
                    {"value": {"action_requests": actions}, "id": "int-1"},
                ]
            },
        )
        self.assertTrue(interrupted)
        payload = [data for name, data in events if name == "interrupt"][0]
        self.assertEqual(payload["interrupt_type"], "hitl_approval")
        self.assertEqual(payload["tool_name"], "order_create")
        self.assertEqual(payload["tool_description"], "创建采购订单")
        self.assertEqual(payload["order_data"]["orderNumber"], "PO1")

    async def test_sdk_stream_sequence_through_adapter(self):
        """模拟 chat_stream 的派发：控制事件不出业务事件，其余按模式进 ChatTurn。"""
        turn = self.turn()
        parts = [
            ("metadata", {"run_id": "run-1"}),
            ("values", {"phase": "executing"}),
            ("custom|analyst:1", {"type": "rubric_evaluation_end", "result": "satisfied"}),
            (
                "messages|analyst:1",
                [{"type": "ai", "content": "库存充足"}, {"tags": []}],
            ),
            ("end", {}),
        ]
        names = []
        for event, data in parts:
            adapted = adapt_stream_part(event, data)
            if adapted is None:
                continue
            namespace, mode, payload = adapted
            if mode == "values":
                events, _ = await turn.handle_values(namespace, payload)
            elif mode == "custom":
                events = await turn.handle_custom(payload)
            else:
                events = await turn.handle_message(namespace, payload)
            names.extend(name for name, _ in events)

        self.assertEqual(names, ["phase", "review_result", "thinking", "token"])
        self.assertEqual(turn.phase, "executing")
        self.assertEqual(turn.assistant_content, "库存充足")

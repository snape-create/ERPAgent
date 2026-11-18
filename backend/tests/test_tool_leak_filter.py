"""内部结构化调用不得以业务工具的形式冒泡到前端。

W1c-fix3：``OrderIntentDecision`` / ``ReviewRoute`` / ``grade_rubric`` 这类
``with_structured_output`` 绑定出来的工具名，在 SDK 流里可能晚于 args 片段出现
（甚至只在第 3 个片段才带 name）。参数字节必须先缓冲，等名称确认后再决定
「补发」还是「整体丢弃」，且不能影响正常业务工具的完整事件序列。
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import chat_turn
from app.services.chat_turn import ChatTurn


def ai_chunk(chunks, *, message_id=None):
    message = {"type": "ai", "content": "", "tool_call_chunks": chunks}
    if message_id:
        message["id"] = message_id
    return [message, {"tags": []}]


def chunk(name=None, args="", call_id=None, index=None):
    item = {"name": name, "args": args, "id": call_id}
    if index is not None:
        item["index"] = index
    return item


def tool_result(name, call_id, content="ok"):
    return [
        {
            "type": "tool",
            "name": name,
            "tool_call_id": call_id,
            "content": content,
        },
        {"tags": []},
    ]


class ToolLeakFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patch = patch.object(chat_turn, "save_display_messages", AsyncMock())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def turn(self):
        return ChatTurn("thread-leak", [])

    async def test_f1_args_before_name_are_not_emitted(self):
        """F1：名称在第 3 个片段才出现，前 2 个片段的 tool_args 也不得输出。"""
        turn = self.turn()
        first = await turn.handle_message(
            [],
            ai_chunk([
                chunk(args='{"'),
                chunk(args="create"),
                chunk(name="OrderIntentDecision", args="_order", call_id="call-1"),
            ]),
        )
        self.assertEqual(
            [name for name, _ in first if name in ("tool_start", "tool_args")], []
        )

        rest = await turn.handle_message(
            [],
            ai_chunk([chunk(args='": "下单"}', call_id="call-1")]),
        )
        self.assertEqual([name for name, _ in rest], [])
        self.assertEqual(turn.tool_calls, [])

    async def test_f1_split_across_message_fragments(self):
        """F1：名称晚到时，前几条只带 args 的消息也不能提前放行。"""
        turn = self.turn()
        for args in ('{"', "create", "_order"):
            events = await turn.handle_message([], ai_chunk([chunk(args=args)]))
            self.assertEqual([name for name, _ in events], [])

        late_name = await turn.handle_message(
            [], ai_chunk([chunk(name="OrderIntentDecision", args='": "下单"}')])
        )
        self.assertEqual([name for name, _ in late_name], [])

        result = await turn.handle_message(
            [], tool_result("OrderIntentDecision", "call-1")
        )
        self.assertEqual(result, [])

    async def test_f2_all_internal_names_suppressed(self):
        """F2：OrderIntentDecision / ReviewRoute / grade_rubric 全部被抑制。"""
        for name in ("OrderIntentDecision", "ReviewRoute", "grade_rubric"):
            with self.subTest(name=name):
                turn = self.turn()
                events = await turn.handle_message(
                    [],
                    ai_chunk([
                        chunk(name=name, args='{"a"', call_id=f"call-{name}"),
                        chunk(args=": 1}", call_id=f"call-{name}"),
                    ]),
                )
                self.assertEqual(events, [])
                self.assertEqual(turn.tool_calls, [])

                result = await turn.handle_message(
                    [], tool_result(name, f"call-{name}")
                )
                self.assertEqual(result, [])

    async def test_f2_review_router_tags_drop_whole_message(self):
        """F2：tags 含 review_router 的消息整体丢弃（含 token / 参数）。"""
        turn = self.turn()
        events = await turn.handle_message(
            [],
            [
                {
                    "type": "ai",
                    "content": "route decision",
                    "tool_call_chunks": [chunk(name="ReviewRoute", args='{"a": 1}')],
                },
                {"tags": ["review_router"]},
            ],
        )
        self.assertEqual(events, [])

        continuation = await turn.handle_message(
            [],
            ai_chunk([chunk(args='{"more"'), chunk(args=": 2}")]),
        )
        self.assertEqual(continuation, [])
        self.assertEqual(turn.tool_calls, [])

    async def test_f3_business_tool_keeps_full_sequence(self):
        """F3：业务工具仍按 tool_start → tool_args → tool_result → tool_end 输出。"""
        turn = self.turn()
        events = await turn.handle_message(
            [],
            ai_chunk([
                chunk(name="get_inventory", args='{"part', call_id="call-9"),
                chunk(args='_no": 1}', call_id="call-9"),
            ]),
        )
        self.assertEqual(
            [name for name, _ in events],
            ["thinking", "tool_start", "tool_args", "tool_args"],
        )
        self.assertEqual(events[1][1], {"name": "get_inventory", "id": "call-9"})
        self.assertEqual(turn.tool_calls[0]["args"], '{"part_no": 1}')

        result = await turn.handle_message([], tool_result("get_inventory", "call-9"))
        self.assertEqual([name for name, _ in result], ["tool_result", "tool_end"])
        self.assertEqual(result[0][1]["name"], "get_inventory")
        self.assertEqual(result[1][1], {"id": "call-9"})

    async def test_f3_business_tool_with_late_name_keeps_events(self):
        """F3：名称晚到的正常工具仍补齐 tool_start，不丢参数。"""
        turn = self.turn()
        first = await turn.handle_message(
            [],
            ai_chunk([
                chunk(args='{"part', index=0),
                chunk(args='_no": 1}', index=0),
                chunk(name="create_order", args="", call_id="call-7", index=0),
            ]),
        )
        self.assertEqual(
            [name for name, _ in first],
            ["thinking", "tool_start", "tool_args", "tool_args"],
        )
        self.assertEqual(first[1][1], {"name": "create_order", "id": "call-7"})
        self.assertEqual(turn.tool_calls[0]["args"], '{"part_no": 1}')

        result = await turn.handle_message([], tool_result("create_order", "call-7"))
        self.assertEqual([name for name, _ in result], ["tool_result", "tool_end"])

    async def test_internal_then_business_in_same_turn(self):
        """内部调用被丢弃后，同一轮的正常工具不受影响。"""
        turn = self.turn()
        leaked = await turn.handle_message(
            [],
            ai_chunk([
                chunk(args='{"'),
                chunk(name="OrderIntentDecision", args="x", call_id="call-bad"),
            ]),
        )
        self.assertEqual(leaked, [])

        business = await turn.handle_message(
            [],
            ai_chunk([
                chunk(name="get_inventory", args='{"part_no": 1}', call_id="call-ok"),
            ]),
        )
        self.assertEqual(
            [name for name, _ in business],
            ["thinking", "tool_start", "tool_args"],
        )
        self.assertEqual([call["name"] for call in turn.tool_calls], ["get_inventory"])


if __name__ == "__main__":
    unittest.main()

"""网关切到 langgraph-sdk 的单测：FakeClient，不连真服务。

覆盖验收闸门：
- C2：stream_mode / stream_subgraphs / config.configurable / run 前 threads.create
- C3：同一份 SDK 流的事件名与字段逐条符合预期
- C4：resume 只传 command（dict），update 仅在 decisions 非空时出现
另覆盖服务端 error 事件的统一壳、``/state`` 的 dict 状态分支、客户端懒加载单例。
"""
import json
import unittest
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.types import Command

from app.gateway.routers import chat as chat_router
from app.services import chat_stream, chat_turn, langgraph_client
from app.services.langgraph_client import server_thread_id

THREAD_ID = "thread-1"
SDK_THREAD_ID = server_thread_id(THREAD_ID)
USER_ID = "user-1"
USERNAME = "小明"

RUBRIC_END = {
    "type": "rubric_evaluation_end",
    "result": "satisfied",
    "explanation": "满足要求",
    "criteria": [],
    "iteration": 1,
}
TODOS = [{"content": "查库存", "status": "completed"}]

# server 路径：SDK StreamPart 的实际形状（dict 消息、事件名带 namespace）
SDK_PARTS = [
    ("metadata", {"run_id": "run-1"}),
    ("values", {"phase": "executing"}),
    (
        "messages",
        [
            {
                "type": "ai",
                "content": "",
                "tool_call_chunks": [
                    {"name": "query_stock", "args": '{"part_no": 1}', "id": "call-1"}
                ],
            },
            {"tags": []},
        ],
    ),
    (
        "messages",
        [
            {
                "type": "tool",
                "name": "query_stock",
                "tool_call_id": "call-1",
                "content": "库存 3 件",
            },
            {"tags": []},
        ],
    ),
    ("custom|analyst:1", RUBRIC_END),
    ("messages|analyst:1", [{"type": "ai", "content": "库存充足"}, {"tags": []}]),
    ("values", {"phase": "reviewing"}),
    ("values", {"todos": TODOS}),
    ("end", {"status": "success"}),
]

EXPECTED_EVENTS = [
    "thinking",
    "thinking",
    "phase",
    "thinking",
    "tool_start",
    "tool_args",
    "tool_result",
    "tool_end",
    "review_result",
    "token",
    "phase",
    "todo_update",
    "done",
]


def parse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    """SSE 线格式 → [(event_name, payload)]。"""
    events = []
    for chunk in chunks:
        lines = chunk.split("\n")
        events.append((
            lines[0].removeprefix("event: "),
            json.loads(lines[1].removeprefix("data: ")),
        ))
    return events


class FakeStream:
    """可迭代的 StreamPart 流；记录是否被 aclose。"""

    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event, data in self.parts:
            yield SimpleNamespace(event=event, data=data)

    async def aclose(self):
        self.closed = True


class FakeThreads:
    def __init__(self, client):
        self.client = client
        self.state = {}

    async def create(self, **kwargs):
        self.client.calls.append(("threads.create", kwargs))
        return {"thread_id": kwargs.get("thread_id")}

    async def get_state(self, thread_id):
        self.client.calls.append(("threads.get_state", {"thread_id": thread_id}))
        return self.state


class FakeRuns:
    def __init__(self, client, parts):
        self.client = client
        self.stream_obj = FakeStream(parts)

    def stream(self, thread_id, assistant_id, **kwargs):
        self.client.calls.append((
            "runs.stream",
            {"thread_id": thread_id, "assistant_id": assistant_id, **kwargs},
        ))
        return self.stream_obj


class FakeClient:
    """记录调用顺序，供“run 前必须 create thread”断言使用。"""

    def __init__(self, parts, state=None):
        self.calls: list[tuple[str, dict]] = []
        self.threads = FakeThreads(self)
        self.threads.state = state or {}
        self.runs = FakeRuns(self, parts)


class ChatStreamSdkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patch = patch.object(chat_turn, "save_display_messages", AsyncMock())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    async def collect(self, *, message="下单", resume_data=None, parts=None):
        """跑一遍 stream_chat_response，返回 (FakeClient, SSE 文本列表)。"""
        client = FakeClient(parts if parts is not None else SDK_PARTS)

        with ExitStack() as stack:
            stack.enter_context(patch.object(chat_stream, "get_client", return_value=client))
            stack.enter_context(patch.object(
                chat_stream,
                "get_display_messages",
                AsyncMock(side_effect=lambda *a, **k: []),
            ))
            stack.enter_context(patch.object(chat_stream, "save_display_messages", AsyncMock()))
            for name in ("save_display_messages", "save_harness_trace", "save_conversation"):
                stack.enter_context(patch.object(chat_turn, name, AsyncMock()))

            chunks = [
                chunk
                async for chunk in chat_stream.stream_chat_response(
                    message, THREAD_ID, USER_ID, USERNAME, resume_data
                )
            ]
        return client, chunks

    async def test_server_stream_parameters_and_thread_create(self):
        """C2：stream 参数、用户身份进 configurable、run 之前建 thread；SDK 侧用 UUID。"""
        client, chunks = await self.collect()

        self.assertEqual(
            [name for name, _ in client.calls], ["threads.create", "runs.stream"]
        )
        self.assertEqual(
            client.calls[0][1], {"thread_id": SDK_THREAD_ID, "if_exists": "do_nothing"}
        )

        kwargs = client.calls[1][1]
        self.assertEqual(kwargs["thread_id"], SDK_THREAD_ID)
        self.assertEqual(kwargs["assistant_id"], "lead_agent")
        self.assertEqual(kwargs["input"], {"messages": [{"role": "user", "content": "下单"}]})
        self.assertNotIn("command", kwargs)
        self.assertEqual(kwargs["stream_mode"], ["messages-tuple", "values", "custom"])
        self.assertTrue(kwargs["stream_subgraphs"])
        self.assertEqual(
            kwargs["config"]["configurable"],
            {"thread_id": SDK_THREAD_ID, "user_id": USER_ID, "username": USERNAME},
        )
        # 对外 SSE 仍用原始 thread_id
        events = parse_events(chunks)
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["thread_id"], THREAD_ID)
        # 正常结束后流被关闭
        self.assertTrue(client.runs.stream_obj.closed)

    async def test_event_sequence_and_fields(self):
        """C3：同一份 SDK 流的事件名与字段逐条符合预期。"""
        _, chunks = await self.collect()
        events = parse_events(chunks)

        self.assertEqual([name for name, _ in events], EXPECTED_EVENTS)
        self.assertEqual(events[0][1], {"status": "start", "phase": "initializing"})
        self.assertEqual(events[1][1], {"status": "start"})
        self.assertEqual(events[2][1], {"phase": "executing", "label": "执行中"})
        self.assertEqual(events[3][1], {"status": "end"})
        self.assertEqual(events[4][1], {"name": "query_stock", "id": "call-1"})
        self.assertEqual(events[5][1], {"args": '{"part_no": 1}'})
        self.assertEqual(
            events[6][1],
            {"name": "query_stock", "id": "call-1", "content": "库存 3 件"},
        )
        self.assertEqual(events[7][1], {"id": "call-1"})
        self.assertEqual(
            events[8][1],
            {
                "verdict": "satisfied",
                "explanation": "满足要求",
                "criteria": [],
                "iteration": 1,
            },
        )
        self.assertEqual(events[9][1], {"content": "库存充足", "source": "analyst"})
        self.assertEqual(events[10][1], {"phase": "reviewing", "label": "审查中"})
        self.assertEqual(
            events[11][1],
            {"phase": "planning", "todos": [{"id": "todo-0", "content": "查库存", "status": "completed"}]},
        )
        self.assertEqual(events[12][1], {"thread_id": THREAD_ID, "interrupted": False})

    async def test_resume_sends_command_only(self):
        """C4：resume 只传 command（dict），decisions 非空才带 update。"""
        client, _ = await self.collect(
            message="", resume_data={"decisions": [{"type": "approve"}]}
        )
        kwargs = client.calls[1][1]

        self.assertNotIn("input", kwargs)
        command = kwargs["command"]
        self.assertIsInstance(command, dict)
        self.assertNotIsInstance(command, Command)
        self.assertEqual(command["update"], {"_human_approved": True})
        self.assertEqual(command["resume"]["decisions"], [{"type": "approve"}])

    async def test_resume_without_decisions_has_no_update(self):
        client, _ = await self.collect(message="", resume_data={"decisions": []})
        command = client.calls[1][1]["command"]

        self.assertIsInstance(command, dict)
        self.assertNotIn("update", command)
        self.assertEqual(command["resume"], {"decisions": []})

    async def test_sdk_calls_use_uuid_while_api_keeps_raw_thread_id(self):
        """D3：SDK 侧 create / stream 收到映射后的 UUID，SSE 仍回原始 thread_id。"""
        client, chunks = await self.collect()

        sdk_ids = [
            payload["thread_id"]
            for name, payload in client.calls
            if name in ("threads.create", "runs.stream")
        ]
        self.assertEqual(sdk_ids, [SDK_THREAD_ID, SDK_THREAD_ID])
        self.assertNotIn(THREAD_ID, sdk_ids)
        self.assertEqual(parse_events(chunks)[-1][1]["thread_id"], THREAD_ID)

    async def test_server_error_event_uses_gateway_shell(self):
        parts = [("error", {"error": "ValueError", "message": "boom"})]
        _, chunks = await self.collect(parts=parts)
        events = parse_events(chunks)

        self.assertEqual([name for name, _ in events], ["thinking", "thinking", "error", "done"])
        self.assertIn("boom", events[2][1]["message"])
        self.assertFalse(events[3][1]["interrupted"])


class ChatStateServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_server_reads_dict_interrupt(self):
        actions = [{
            "name": "order_create",
            "args": {"orderNumber": "PO1", "orderDetail": [{"partId": 1, "quantity": 2}]},
            "description": "创建采购订单",
        }]
        state = {
            "next": ["approval"],
            "tasks": [{"interrupts": [{"value": {"action_requests": actions}, "id": "int-1"}]}],
        }
        client = FakeClient([], state=state)

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                chat_router, "get_conversation_user_id", AsyncMock(return_value=USER_ID)
            ))
            stack.enter_context(patch.object(chat_router, "get_client", return_value=client))
            payload = await chat_router.chat_state(THREAD_ID)

        self.assertTrue(payload["interrupted"])
        self.assertEqual(payload["thread_id"], THREAD_ID)
        self.assertEqual(payload["interrupt"]["interrupt_type"], "hitl_approval")
        self.assertEqual(payload["interrupt"]["tool_name"], "order_create")
        self.assertEqual(payload["interrupt"]["order_data"]["orderNumber"], "PO1")
        self.assertEqual(
            client.calls, [("threads.get_state", {"thread_id": SDK_THREAD_ID})]
        )


class ServerThreadIdMappingTests(unittest.TestCase):
    """D2：应用 thread_id → 服务端 UUID 的确定性映射。"""

    def test_same_input_same_output(self):
        self.assertEqual(server_thread_id("smoke-t1"), server_thread_id("smoke-t1"))

    def test_different_inputs_differ(self):
        self.assertNotEqual(server_thread_id("smoke-t1"), server_thread_id("smoke-t2"))

    def test_result_is_valid_uuid(self):
        parsed = uuid.UUID(server_thread_id("smoke-t1"))
        self.assertEqual(parsed.version, 5)

    def test_does_not_use_random_uuid4(self):
        """映射必须确定性：uuid4 一旦被调用就直接失败。"""
        with patch.object(
            langgraph_client.uuid, "uuid4", side_effect=AssertionError("uuid4 不可用于映射")
        ):
            first = server_thread_id("smoke-t1")
            second = server_thread_id("smoke-t1")
        self.assertEqual(first, second)


class LangGraphClientTests(unittest.TestCase):
    def test_get_client_is_lazy_singleton(self):
        fake = object()
        with ExitStack() as stack:
            stack.enter_context(patch.object(langgraph_client, "_client", None))
            factory = stack.enter_context(patch.object(
                langgraph_client, "get_sdk_client", return_value=fake
            ))
            stack.enter_context(patch.object(
                langgraph_client,
                "get_gateway_config",
                return_value=SimpleNamespace(langgraph_url="http://lg:2024"),
            ))
            self.assertIs(langgraph_client.get_client(), fake)
            self.assertIs(langgraph_client.get_client(), fake)

        factory.assert_called_once_with(url="http://lg:2024")

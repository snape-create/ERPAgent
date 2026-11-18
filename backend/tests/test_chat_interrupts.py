"""Exercise the actual SSE adapter without Docker, MongoDB or model calls.

走 SDK 路径：FakeClient 产出真实形状的 StreamPart，中断项包成 ``{"value", "id"}``。
"""
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import chat_stream, chat_turn

THREAD_ID = "test-thread"


class FakeStream:
    def __init__(self, parts):
        self.parts = parts

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event, data in self.parts:
            yield SimpleNamespace(event=event, data=data)

    async def aclose(self):
        pass


class FakeThreads:
    async def create(self, **kwargs):
        return {"thread_id": kwargs.get("thread_id")}


class FakeRuns:
    def __init__(self, parts):
        self.stream_obj = FakeStream(parts)

    def stream(self, thread_id, assistant_id, **kwargs):
        return self.stream_obj


class FakeClient:
    def __init__(self, parts):
        self.threads = FakeThreads()
        self.runs = FakeRuns(parts)


class ChatInterruptTests(unittest.IsolatedAsyncioTestCase):
    async def run_stream(self, parts):
        client = FakeClient(parts)
        with ExitStack() as stack:
            stack.enter_context(patch.object(chat_stream, "get_client", return_value=client))
            # 读写在 repositories 里，按模块分别替换（各模块自己持有引用）。
            # get_display_messages 的返回值会被直接 .append()，必须给真 list；
            # 否则返回的是 AsyncMock，append 退化成"协程未 await"的 RuntimeWarning。
            for module in (chat_stream, chat_turn):
                for name in ['get_display_messages', 'save_display_messages', 'save_harness_trace', 'save_conversation']:
                    if hasattr(module, name):
                        fake = AsyncMock(return_value=[] if name == 'get_display_messages' else None)
                        stack.enter_context(patch.object(module, name, fake))
            chunks = [
                x async for x in chat_stream.stream_chat_response('test', THREAD_ID, 'test-user')
            ]
        return [(c.split('\n')[0][7:], json.loads(c.split('\n')[1][6:])) for c in chunks]

    async def test_modern_approval_interrupt(self):
        actions = [{'name': 'order_create', 'args': {'orderNumber': 'PO1', 'orderDetail': [{'partId': 1, 'quantity': 2, 'unitPrice': 3.5}]}, 'description': '创建采购订单'}]
        parts = [('values', {'__interrupt__': [{'value': {'action_requests': actions}, 'id': 'int-1'}]})]
        events = await self.run_stream(parts)
        payload = [v for k, v in events if k == 'interrupt'][0]
        # action_requests 会被拍平成可渲染字段，前端审批卡片据此画出单据
        self.assertEqual(payload['tool_name'], 'order_create')
        self.assertEqual(payload['tool_description'], '创建采购订单')
        self.assertEqual(payload['order_data']['orderNumber'], 'PO1')
        self.assertEqual(payload['action_count'], 1)
        self.assertTrue(events[-1][1]['interrupted'])

    async def test_legacy_interrupt(self):
        parts = [('values', {'interrupts': [{'value': {'action_requests': []}, 'id': 'int-2'}]})]
        events = await self.run_stream(parts)
        self.assertTrue(events[-1][1]['interrupted'])

    async def test_missing_fields_interrupt(self):
        parts = [('values', {'__interrupt__': [{'value': {'type': 'order_info_request', 'missing_fields': ['quantity']}, 'id': 'int-3'}]})]
        events = await self.run_stream(parts)
        payload = [v for k, v in events if k == 'interrupt'][0]
        self.assertEqual(payload['interrupt_type'], 'order_info_supplement')
        self.assertEqual(payload['missing_fields'], ['quantity'])

    async def test_uninterrupted_completion(self):
        events = await self.run_stream([('values', {})])
        self.assertFalse(events[-1][1]['interrupted'])
        self.assertFalse(any(k == 'interrupt' for k, v in events))

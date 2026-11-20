"""单轮对话的 SSE 事件状态机。

把「Agent 流式输出」翻译成前端事件（token / tool_start / tool_result / phase /
todo_update / review_result / interrupt），并负责展示消息的节流持久化。

一个 ``ChatTurn`` 实例 = 一次请求；调用方只做三件事：
    拿到事件 → 通过 SSE 下发 → 出错时兜底保存
"""
import asyncio
import copy
import json
import re
import time
import uuid
from datetime import datetime

from app.repositories.conversations import (
    save_conversation,
    save_display_messages,
    save_harness_trace,
)
from app.services.hitl import build_interrupt_event, enrich_status_change
from app.services.langgraph_adapter import message_field, unwrap_interrupt
from app.services.sse import PERSIST_INTERVAL_SECONDS
from erpflow.log_utils import web_logger

# Harness 阶段 → 前端展示文案（进度由阶段条表达，文案保持纯文字）
PHASE_LABELS = {
    "planning": "规划中",
    "executing": "执行中",
    "reviewing": "审查中",
    "result": "已完成",
    "thinking": "思考中",
}

# 内部结构化调用（评审器、订单意图分类器）不是业务工具，不该出现在前端工具列表里
# OrderIntentDecision / ReviewRoute 是 with_structured_output 绑定的工具名，会冒泡到 messages 流
INTERNAL_TOOL_PATTERN = re.compile(r"(grade|rubric|order_?intent|review_?route)", re.I)

# 单条工具结果 / 参数回传前端的截断长度
TOOL_RESULT_LIMIT = 1000
TOOL_RESULT_PREVIEW = 2000
TODO_ARGS_PREVIEW = 2000
TODO_RESULT_PREVIEW = 500

Event = tuple[str, dict]


class ChatTurn:
    """一次请求的事件累积器 + 持久化节流器。"""

    def __init__(self, thread_id: str, display_messages: list) -> None:
        self.thread_id = thread_id
        self.display_messages = display_messages

        # 整轮起止时间（毫秒），供前端展示"用时 12.4s"；结束前 ended_at 保持 None
        self.started_at = int(time.time() * 1000)
        self.ended_at: int | None = None

        # 当前助手消息累积缓冲
        self.assistant_content = ""
        self.tool_calls: list[dict] = []
        # 与 content/toolCalls 并行的有序分段：前端按它还原"正文→工具→正文"，
        # 只存聚合视图会让工具全部堆到正文上方。tool part 直接引用 tool_calls
        # 里的同一个 dict，参数/结果更新后无需二次同步。
        self.assistant_parts: list[dict] = []
        # 工具调用之后模型会开一段新文本，两段之间没有分隔符就会连成
        # "……定位零件。✅ 已获取数据" 这种一句话。这里记一笔，续写前补空行。
        self.pending_separator = False
        self.thinking_emitted = False

        # 最终稿在 parts 中的起点：评审要求修订后，由此下标起的 parts 属于最终稿，
        # 之前的都是过程/草稿。前端据此切分展示，历史消息仍保留全部草稿。
        self.final_start_index = 0
        # 第一次修订的起点：多次修订时保持不变，供前端识别「这段回答被修订过」
        self.first_revision_index: int | None = None
        # 评审已判定 needs_revision，但还没有等到修订后的首段输出。
        self._revision_pending = False
        # 修订稿必须写进新的 text part：否则会续写进草稿段落，切分点失效。
        self._revision_boundary_pending = False

        self.phase = "thinking"
        self.last_phase = "thinking"
        self.last_todos_sig = None
        self.last_review_sig = None
        self.harness_trace: list[dict] = []

        self._assistant_entry: dict | None = None
        self.assistant_appended = False
        self._last_persist = 0.0
        # 落库在后台任务里做：token 热路径上 await Mongo 会把 SSE 卡住。
        # 同一时刻只允许一个写任务，期间的新快照只打 dirty 标记，由该任务补写。
        self._persist_task: asyncio.Task | None = None
        self._persist_dirty = False

        # 内部调用（review_router 等）一旦确认，后续增量也必须一并丢弃。
        # SDK 序列化后 tags / name / id 都可能只在后续片段才出现，所以参数字节
        # 先按调用缓冲，等名称出现后再决定「补发」还是「整体丢弃」。
        self._suppressed_call_ids: set[str] = set()
        self._suppressed_message_ids: set[str] = set()
        self._call_buffers: dict[tuple, dict] = {}
        self._last_call_key: tuple | None = None
        self._call_seq = 0
        self._current_message_id = ""
        self._internal_message_open = False

    # ------------------------------------------------------------------ 持久化
    def _entry(self) -> dict:
        """惰性创建助手消息条目，并同步当前内容/工具调用。"""
        if self._assistant_entry is None:
            self._assistant_entry = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": "",
                "toolCalls": None,
                "parts": None,
                "timestamp": datetime.now().isoformat(),
            }
        self._assistant_entry["content"] = self.assistant_content
        self._assistant_entry["toolCalls"] = self.tool_calls or None
        self._assistant_entry["parts"] = self.assistant_parts or None
        # 最终稿起点：前端按 parts[finalStartIndex:] 取最终稿，之前视为过程/草稿
        self._assistant_entry["finalStartIndex"] = self.final_start_index
        # 首次修订起点：未修订过为 None，前端据此判断有无修订痕迹
        self._assistant_entry["firstRevisionIndex"] = self.first_revision_index
        # 每次同步起止时间：流式期间 endedAt 为 None，结束时再写入最终值
        self._assistant_entry["startedAt"] = self.started_at
        self._assistant_entry["endedAt"] = self.ended_at
        return self._assistant_entry

    def _has_assistant_payload(self) -> bool:
        """本轮是否产生了需要持久化的助手内容。

        不能只看 assistant_content：模型可能先调用 generate_document /
        download_sandbox_file 等工具，随后没有输出正文（或客户端断流）。
        如果此时丢弃 tool_calls，历史里就只剩用户消息，前端也无法再展示
        工具结果里的产物链接。
        """
        return bool(self.assistant_content or self.tool_calls)

    def snapshot(self) -> list:
        """当前进度的完整快照（含尚未写完的助手消息）。"""
        entries = list(self.display_messages)
        if self._has_assistant_payload() and not self.assistant_appended:
            entries.append(dict(self._entry()))
        return entries

    async def persist(self) -> None:
        await save_display_messages(self.thread_id, self.snapshot())

    async def _persist_loop(self) -> None:
        """后台写库：每次写最新快照，写的过程中有新内容就再补一轮。

        ``snapshot()`` 返回的列表/字典仍与主流程共享，必须 deepcopy 后再交出去，
        否则 await 期间主流程继续追加 token 或改 tool_calls，序列化出的内容会自相矛盾。
        """
        try:
            while True:
                await save_display_messages(
                    self.thread_id, copy.deepcopy(self.snapshot())
                )
                if not self._persist_dirty:
                    return
                self._persist_dirty = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 后台任务没人 await，异常必须自己消化，否则会变成 unhandled task exception
            web_logger.warning(
                f"Failed to persist progress for {self.thread_id}: {exc}"
            )

    def _schedule_persist(self) -> None:
        """调度一次后台写库；已有任务在跑时只标 dirty，不并发写同一份消息。"""
        if self._persist_task is not None and not self._persist_task.done():
            self._persist_dirty = True
            return
        self._persist_dirty = False
        self._persist_task = asyncio.create_task(self._persist_loop())

    async def flush_persist(self) -> None:
        """等掉在途的后台写，再保存最新快照（结束 / 中断时调用）。"""
        task = self._persist_task
        self._persist_task = None
        self._persist_dirty = False
        if task is not None:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                # 取消或写失败都不影响最终这次保存；失败原因已在 _persist_loop 记录
                pass
        await self.persist()

    async def maybe_persist(self, force: bool = False) -> None:
        """按间隔节流保存：既能看到已写出的内容，又不会每个 token 写一次库。

        只做节流判断和调度，不在这里 await 数据库，避免阻塞 SSE 下发。
        """
        now = time.monotonic()
        if force or now - self._last_persist >= PERSIST_INTERVAL_SECONDS:
            self._last_persist = now
            self._schedule_persist()

    def _mark_revision(self) -> list[Event]:
        """把当前 parts 长度记为最终稿起点，并通知前端切换展示。

        评审要求修订时调用：此前的 parts 是过程/草稿（保留），此后的输出是最终稿。
        多次修订时 firstRevisionIndex 只记第一次，finalStartIndex 每次更新。
        """
        if self.first_revision_index is None:
            self.first_revision_index = len(self.assistant_parts)
        self.final_start_index = len(self.assistant_parts)
        self._revision_pending = False
        # 下一段正文要另起 part，避免被合并进草稿段落导致切分点对不上
        self._revision_boundary_pending = True
        return [("revision", {
            "finalStartIndex": self.final_start_index,
            "firstRevisionIndex": self.first_revision_index,
        })]

    def _append_text_part(self, text: str) -> None:
        """把一段正文追加到最后一个 text part；中间隔着工具就新起一段。"""
        if not text:
            return
        if (
            not self._revision_boundary_pending
            and self.assistant_parts
            and self.assistant_parts[-1].get("type") == "text"
        ):
            self.assistant_parts[-1]["text"] += text
        else:
            self.assistant_parts.append({"type": "text", "text": text})
        self._revision_boundary_pending = False

    def append_assistant(self) -> None:
        """把本轮助手消息并入 display_messages（流结束或审批中断时调用一次）。

        纯工具轮次（有 tool_calls、无正文）也要落库，否则 HTML/图表等
        产物链接只存在于 SSE 流里，刷新页面后整个助手消息都会消失。
        """
        if self._has_assistant_payload() and not self.assistant_appended:
            self.display_messages.append(self._entry())
            self.assistant_appended = True

    async def finish_interrupted(self) -> None:
        """中断结束：先固化消息与 trace，避免待审批轮次在库里是空的。"""
        # 先记结束时间，再 append：最终快照要带上 endedAt，流式中途的快照仍为 None
        self.ended_at = int(time.time() * 1000)
        self.append_assistant()
        # 后台可能还有一次旧快照在写，先 flush 再落最终版，避免旧快照覆盖新内容
        await self.flush_persist()
        await save_harness_trace(self.thread_id, self.harness_trace)

    async def finish_completed(self, user_id: str, message: str = "") -> None:
        """正常结束：固化消息 + trace，并给首条消息补一个会话标题。"""
        # 先记结束时间，再 append：最终快照要带上 endedAt，流式中途的快照仍为 None
        self.ended_at = int(time.time() * 1000)
        self.append_assistant()
        await self.flush_persist()
        await save_harness_trace(self.thread_id, self.harness_trace)
        if message:
            await save_conversation(
                self.thread_id, user_id, conversation_title(message)
            )

    # -------------------------------------------------------------- 事件分发
    async def handle_values(self, namespace, chunk) -> tuple[list[Event], bool]:
        """处理 values 流：结构化状态（阶段 / todo / 评审 / 中断）。

        Returns:
            (事件列表, 是否命中中断)。命中中断时调用方应立即收尾并结束本轮。
        """
        events: list[Event] = []
        if not isinstance(chunk, dict):
            return events, False

        # 评审路由的判定（只在主图，不含子图）
        if not namespace and chunk.get("review_decision"):
            decision = chunk["review_decision"]
            signature = json.dumps(decision, ensure_ascii=False, sort_keys=True)
            if signature != self.last_review_sig:
                self.last_review_sig = signature
                self.harness_trace.append({
                    "type": "review_decision",
                    **decision,
                    "timestamp": datetime.now().isoformat(),
                })

        # 阶段流转（来自 HarnessPhaseMiddleware 写入的 state.phase）
        new_phase = chunk.get("phase")
        if new_phase and new_phase != self.last_phase:
            self.last_phase = new_phase
            self.phase = new_phase
            label = PHASE_LABELS.get(new_phase, new_phase)
            self.harness_trace.append({
                "type": "phase",
                "phase": new_phase,
                "label": label,
                "timestamp": datetime.now().isoformat(),
            })
            events.append(("phase", {"phase": new_phase, "label": label}))

        # todo 列表（来自 TodoListMiddleware 的 write_todos 状态）
        todos = chunk.get("todos")
        if todos is not None:
            todos_sig = json.dumps(todos, ensure_ascii=False, sort_keys=True)
            if todos_sig != self.last_todos_sig:
                self.last_todos_sig = todos_sig
                plan_items = [
                    {
                        "id": f"todo-{index}",
                        "content": item.get("content", ""),
                        "status": item.get("status", "pending"),
                    }
                    for index, item in enumerate(todos)
                ]
                if plan_items:
                    events.append(("todo_update", {"phase": "planning", "todos": plan_items}))

        # 中断检测：LangGraph 发 __interrupt__，同时兼容旧的 interrupts 键
        # SDK 流里的中断项是 {"value": ..., "id": ...}，先解包再整形
        interrupts = chunk.get("__interrupt__") or chunk.get("interrupts")
        if interrupts:
            for interrupt_item in interrupts:
                value = unwrap_interrupt(interrupt_item)
                if not isinstance(value, dict):
                    continue
                payload = build_interrupt_event(value)
                if payload.get("interrupt_type") == "hitl_approval":
                    payload = await enrich_status_change(payload)
                events.append(("interrupt", payload))
            return events, True

        return events, False

    async def handle_custom(self, chunk) -> list[Event]:
        """处理 custom 流：评审器等中间件推的结构化事件。"""
        events: list[Event] = []
        if not isinstance(chunk, dict):
            return events

        event_type = chunk.get("type", "")
        if event_type == "rubric_evaluation_start":
            # 评审器开始 grading → 进入审查阶段
            if self.phase != "reviewing":
                self.phase = "reviewing"
                self.last_phase = "reviewing"
                events.append(("phase", {"phase": "reviewing", "label": "审查中"}))
        elif event_type == "rubric_evaluation_end":
            # 评审未通过、将打回重做：这里只置标记，等修订稿真正开始时再定起点
            if chunk.get("result") == "needs_revision":
                self._revision_pending = True
            review_data = {
                "verdict": chunk.get("result", ""),
                "explanation": chunk.get("explanation", ""),
                "criteria": chunk.get("criteria", []),
                "iteration": chunk.get("iteration", 0),
            }
            self.harness_trace.append({
                "type": "review",
                **review_data,
                "timestamp": datetime.now().isoformat(),
            })
            events.append(("review_result", review_data))
        return events

    async def handle_message(self, namespace, chunk) -> list[Event]:
        """处理 messages 流：工具调用、工具结果、助手文本。"""
        events: list[Event] = []
        # 路由器的判定是内部控制数据，既不是回答也不是用户可见的工具调用
        metadata = chunk[1] if isinstance(chunk, (tuple, list)) and len(chunk) > 1 else {}
        tags = metadata.get("tags") if isinstance(metadata, dict) else None
        internal_tag = "review_router" in (tags or [])

        token = chunk[0] if isinstance(chunk, (list, tuple)) and len(chunk) >= 1 else chunk
        if token is None:
            return events

        # 消息可能是 LangChain 对象（进程内）或 SDK 序列化后的 dict，统一按字段名读
        token_type = message_field(token, "type", "")
        content = message_field(token, "content", "")
        tool_call_chunks = message_field(token, "tool_call_chunks", None)
        message_id = message_field(token, "id", "") or ""
        # 名称 / 附加参数后面多处要用，先取出来复用
        source_name = message_field(token, "name")
        extra = message_field(token, "additional_kwargs", {}) or {}

        # rubric_grader 注入的 human 消息 = 评审要求修订：从这一刻起后面的输出是最终稿
        if token_type == "human" and (
            source_name == "rubric_grader"
            or (isinstance(extra, dict) and extra.get("lc_source") == "rubric_grader")
        ):
            return self._mark_revision()

        # 换了一条 AI 消息：上一条消息的工具缓冲随之失效。
        # （msg id 缺失的片段沿用当前消息；工具结果要复用缓冲，不能清）
        if message_id and token_type != "tool":
            if self._current_message_id and message_id != self._current_message_id:
                self._call_buffers.clear()
                self._last_call_key = None
            self._current_message_id = message_id

        # 首个片段带 review_router 标签：登记这条消息 / 这组 tool_call，后续片段不再放行
        if internal_tag:
            self._suppress_internal(message_id, tool_call_chunks)
            return events

        if message_id:
            # 换了一条消息，上一条内部消息的退化标记随之失效
            if message_id not in self._suppressed_message_ids:
                self._internal_message_open = False
            else:
                return events

        # 没有 id 的内部消息（token / tool_args / tool_result 片段）按顺序整体丢弃
        if not message_id and self._internal_message_open:
            return events

        # AIMessage with tool_call_chunks → tool_start / tool_args
        if tool_call_chunks:
            # 修订稿以工具调用开头：先标记最终稿起点，再追加工具 part
            if self._revision_pending:
                events.extend(self._mark_revision())
            for call in tool_call_chunks:
                call_name = message_field(call, "name") or ""
                call_args = message_field(call, "args") or ""
                call_id = message_field(call, "id", "") or ""
                call_index = message_field(call, "index", None)

                key = self._resolve_call_key(call_id, call_name, call_index)
                self._last_call_key = key
                buffer = self._call_buffer(key)
                if call_id and not buffer["id"]:
                    buffer["id"] = call_id

                # 名称可能晚到：一旦命中内部模式，整个调用（含已缓冲参数）一起丢弃
                if call_name:
                    buffer["name"] = call_name
                if buffer["id"] and buffer["id"] in self._suppressed_call_ids:
                    buffer["suppressed"] = True
                if buffer["name"] and INTERNAL_TOOL_PATTERN.search(buffer["name"]):
                    buffer["suppressed"] = True
                if buffer["suppressed"]:
                    if buffer["id"]:
                        self._suppressed_call_ids.add(buffer["id"])
                    buffer["args"] = []
                    continue

                if call_args:
                    if buffer["emitted"]:
                        self.tool_calls[buffer["tool_index"]]["args"] += call_args
                        events.append(("tool_args", {"args": call_args}))
                    else:
                        # 名称还没出现：先攒着，避免内部调用的参数提前漏出去
                        buffer["args"].append(call_args)

                if buffer["name"] and not buffer["emitted"]:
                    events.extend(self._emit_tool_start(buffer))
            return events

        # ToolMessage → tool_result / tool_end
        if token_type == "tool":
            events.extend(await self._handle_tool_message(token, content))
            return events

        # AIMessage 纯文本 → token 事件
        # 只把模型（AI）的文本当作回答；跳过 Human/System 注入消息
        # （例如 rubric 评审器注入的英文修订反馈，绝不能展示给用户）。
        is_internal = (
            source_name == "rubric_grader"
            or (isinstance(extra, dict) and extra.get("lc_source") == "rubric_grader")
        )
        is_ai_text = token_type in ("ai", "AIMessageChunk")

        # DeepSeek 把思考过程单独放在 additional_kwargs.reasoning_content 里，
        # 与正文是两路数据。这里单发 reasoning 事件，不并入 assistant_content，
        # 也不参与落库：思考过程不该出现在历史消息里，更不该混进正文。
        reasoning = extra.get("reasoning_content") if isinstance(extra, dict) else None
        if is_ai_text and not is_internal and isinstance(reasoning, str) and reasoning:
            events.append((
                "reasoning",
                {"content": reasoning, "source": source_label(namespace)},
            ))

        # 纯空白分片必须原样透传：流式输出时 "\n\n" 经常单独成一个 chunk，
        # 过滤掉会导致标题粘连正文、列表项挤成一行、表格贴着上一段。
        if (
            is_ai_text
            and not is_internal
            and isinstance(content, str)
            and content != ""
        ):
            # 修订稿的第一段正文：先标记最终稿起点，再写正文
            if self._revision_pending:
                events.extend(self._mark_revision())
            # 可见文本意味着上一组工具调用已经结束，旧调用的兜底指针失效
            self._last_call_key = None
            # 只有出现可见字符才结束 thinking，纯换行不应让"思考中"提前消失
            if content.strip() and not self.thinking_emitted:
                events.append(("thinking", {"status": "end"}))
                self.thinking_emitted = True
            if self.pending_separator and content.strip():
                if self.assistant_content and not self.assistant_content.endswith("\n") \
                        and not content.startswith("\n"):
                    content = "\n\n" + content
                self.pending_separator = False
            self.assistant_content += content
            # 历史消息也要能还原正文与工具的交错顺序，不能只存聚合的 content
            self._append_text_part(content)
            events.append(("token", {"content": content, "source": source_label(namespace)}))
            # 生成过程中按间隔落库，断流后仍能看到已经写出的内容
            await self.maybe_persist()
        return events

    def _resolve_call_key(self, call_id: str, call_name: str, index) -> tuple:
        """为一条 tool_call 片段定位缓冲区。

        ``name`` / ``id`` 可能只在后续片段出现，只有 ``args`` 的增量必须落到
        同一个缓冲区；既无 id 也无 index 时退回「上一次开始的调用」。
        """
        if call_id:
            key = ("id", call_id)
            if key in self._call_buffers:
                return key
            # id 迟到：此前只有 args 的片段先落在无 id 的缓冲里，接管过来
            last_key = self._last_call_key
            last = self._call_buffers.get(last_key) if last_key else None
            if (
                last is not None
                and not last["name"]
                and not last["emitted"]
                and not last["suppressed"]
            ):
                last["id"] = call_id
                self._call_buffers[key] = self._call_buffers.pop(last_key)
                return key
            return key
        if index is not None:
            return ("index", index)

        last_key = self._last_call_key
        last = self._call_buffers.get(last_key) if last_key else None
        # 同一条调用：只有 args 的增量，或名称迟到（此前还没有名称）
        if last is not None and (not call_name or not last["name"]):
            return last_key
        self._call_seq += 1
        return ("seq", self._call_seq)

    def _call_buffer(self, key: tuple) -> dict:
        buffer = self._call_buffers.get(key)
        if buffer is None:
            buffer = {
                "id": "",
                "name": "",
                "args": [],
                "emitted": False,
                "suppressed": False,
                "tool_index": None,
            }
            self._call_buffers[key] = buffer
        return buffer

    def _emit_tool_start(self, buffer: dict) -> list[Event]:
        """名称确认后补发 tool_start，并把此前缓冲的参数按片段顺序补发。"""
        events: list[Event] = []
        if not self.thinking_emitted:
            events.append(("thinking", {"status": "end"}))
            self.thinking_emitted = True
        self.tool_calls.append({
            "id": buffer["id"] or str(uuid.uuid4()),
            "name": buffer["name"],
            "args": "",
            "status": "running",
            "startedAt": int(time.time() * 1000),
        })
        # 工具在消息里的真实位置靠 parts 保留；call 引用同一个 dict，
        # 后续 args/result/status 更新会自动反映到 parts 里。
        self.assistant_parts.append({"type": "tool", "call": self.tool_calls[-1]})
        buffer["tool_index"] = len(self.tool_calls) - 1
        buffer["emitted"] = True
        events.append(("tool_start", {
            "name": buffer["name"],
            "id": buffer["id"],
        }))
        for chunk in buffer["args"]:
            self.tool_calls[-1]["args"] += chunk
            events.append(("tool_args", {"args": chunk}))
        buffer["args"] = []
        return events

    def _suppress_internal(self, message_id: str, tool_call_chunks) -> None:
        """登记一条内部消息：整条消息与其中的 tool_call 后续片段都不再放行。

        SDK 序列化后 ``tags`` 只在首片段出现、tool_call 的 ``id`` 也可能只在首片段
        出现，因此同时记录消息 id、调用 id 和「当前消息仍是内部消息」的标记。
        """
        if message_id:
            self._suppressed_message_ids.add(message_id)
        self._internal_message_open = True
        for call in tool_call_chunks or []:
            call_id = message_field(call, "id", "") or ""
            if not call_id:
                continue
            self._suppressed_call_ids.add(call_id)
            buffer = self._call_buffers.get(("id", call_id))
            if buffer is not None:
                buffer["suppressed"] = True
                buffer["args"] = []
        self._last_call_key = None

    async def _handle_tool_message(self, token, content) -> list[Event]:
        """工具结果：更新 buffer 状态、补 todo 事件、推 tool_result / tool_end。"""
        events: list[Event] = []
        result_name = message_field(token, "name", "") or ""
        tool_name = result_name or "unknown"
        call_id = message_field(token, "tool_call_id", "")
        # 内部调用的结果同样不能冒泡（含 id 缺失、只剩 tool_call_id 的情况）
        if INTERNAL_TOOL_PATTERN.search(str(tool_name)) or (
            call_id and call_id in self._suppressed_call_ids
        ):
            self._last_call_key = None
            return events

        # 名称一直没出现的正常工具：用结果里的工具名补全，避免缺 tool_start
        flush_key = ("id", call_id) if call_id else self._last_call_key
        flush_buffer = self._call_buffers.get(flush_key) if flush_key else None
        if flush_buffer is None and self._last_call_key:
            flush_buffer = self._call_buffers.get(self._last_call_key)
        if (
            result_name
            and flush_buffer is not None
            and not flush_buffer["emitted"]
            and not flush_buffer["suppressed"]
            and not flush_buffer["name"]
        ):
            flush_buffer["name"] = result_name
            if not flush_buffer["id"] and call_id:
                flush_buffer["id"] = call_id
            events.extend(self._emit_tool_start(flush_buffer))
        self._last_call_key = None

        tool_content = content if isinstance(content, str) else str(content)
        # 优先按 tool_call_id 精确匹配（并发工具同名/结果乱序时，只按名字会认错人）
        target = next(
            (
                call for call in self.tool_calls
                if call_id and call["id"] == call_id and call["status"] == "running"
            ),
            None,
        ) or next(
            (
                call for call in self.tool_calls
                if call["name"] == tool_name and call["status"] == "running"
            ),
            None,
        )
        if target is not None:
            target["result"] = tool_content[:TOOL_RESULT_LIMIT]
            target["status"] = "done"
            target["endedAt"] = int(time.time() * 1000)

        # TODO 类工具额外推一次任务列表更新
        if "todo" in tool_name.lower() or "task" in tool_name.lower():
            try:
                todo_args = next(
                    (
                        call["args"] for call in self.tool_calls
                        if call["name"] == tool_name and call.get("args")
                    ),
                    None,
                )
                if todo_args:
                    events.append(("todo_update", {
                        "tool": tool_name,
                        "args": todo_args[:TODO_ARGS_PREVIEW],
                        "result": tool_content[:TODO_RESULT_PREVIEW],
                    }))
            except Exception:
                pass

        events.append(("tool_result", {
            "name": tool_name,
            "id": call_id,
            "content": tool_content[:TOOL_RESULT_PREVIEW],
        }))
        events.append(("tool_end", {"id": call_id}))
        # 工具跑完，模型接下来这段是新的一段话
        self.pending_separator = True
        await self.maybe_persist()
        return events


def source_label(namespace) -> str:
    """按子图 namespace 标出这段文本来自主 Agent 还是哪个子 Agent。"""
    if not namespace:
        return "main"
    namespace_text = str(namespace)
    if "analyst" in namespace_text:
        return "analyst"
    if "order" in namespace_text:
        return "order"
    return "main"


def conversation_title(message: str, limit: int = 20) -> str:
    """会话标题：取首条消息前 20 字。"""
    if not message:
        return "新对话"
    return message[:limit] + "..." if len(message) > limit else message

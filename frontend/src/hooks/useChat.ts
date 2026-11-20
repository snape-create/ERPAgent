"use client";

import { useState, useCallback, useRef } from "react";
import { v4 as uuidv4 } from "uuid";
import {
  ChatMessage,
  ToolCallInfo,
  MessagePart,
  SSEEvent,
  InterruptData,
  TodoItem,
  HarnessPhase,
} from "@/lib/types";
import { streamChat, resumeChat } from "@/lib/api";
import { hasChatArtifact } from "@/lib/artifacts";
import { useSSE } from "./useSSE";

const USER_ID = "user-001";
const USERNAME = "采购管理员";

const VALID_PHASES: ReadonlySet<string> = new Set([
  "thinking",
  "planning",
  "executing",
  "reviewing",
  "done",
]);

/** 历史消息里停在 running 的工具说明上次流已异常结束，加载时统一收口为 error */
function markStrandedMessage(msg: ChatMessage): ChatMessage {
  const hasRunning =
    msg.toolCalls?.some((tc) => tc.status === "running") ||
    msg.parts?.some((p) => p.type === "tool" && p.call.status === "running");
  if (!hasRunning) return msg;

  const markError = (tc: ToolCallInfo): ToolCallInfo =>
    tc.status === "running"
      ? { ...tc, status: "error" as const, endedAt: tc.endedAt ?? Date.now() }
      : tc;

  return {
    ...msg,
    toolCalls: msg.toolCalls?.map(markError),
    parts: msg.parts?.map((p): MessagePart =>
      p.type === "tool" ? { type: "tool", call: markError(p.call) } : p
    ),
  };
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
  const [interruptData, setInterruptData] = useState<InterruptData | null>(null);
  const [threadId, setThreadId] = useState<string>(uuidv4());
  const [error, setError] = useState<string | null>(null);
  const [todoItems, setTodoItems] = useState<TodoItem[]>([]);
  const [todoVisible, setTodoVisible] = useState(false);
  const [phase, setPhase] = useState<HarnessPhase>("idle");
  const [phaseLabel, setPhaseLabel] = useState("");
  const [pendingQueue, setPendingQueue] = useState<string[]>([]);
  // 当前活动文案（"正在理解"/"正在执行 xxx"），供状态胶囊读取
  const [activityLabel, setActivityLabel] = useState("");
  // 最后一个事件的时间戳：界面据此判断流是否已经静默卡住
  const [lastEventAt, setLastEventAt] = useState(0);
  // HTTP 流真正关闭（而不是收到 done）的时间：历史缓存要等最终落库完成再失效
  const [finalizedAt, setFinalizedAt] = useState(0);

  const assistantMsgRef = useRef<string>("");
  const toolCallsRef = useRef<ToolCallInfo[]>([]);
  // 与 content/toolCalls 并行的有序分段，记录正文与工具的交替顺序
  const partsRef = useRef<MessagePart[]>([]);
  // 终稿分界：parts 中该下标之后的 text 才是最终稿，之前的是草稿 / 过程文本
  const finalStartIndexRef = useRef(0);
  // 第一次修订发生时的草稿结束位置：修订期间答案区继续显示这段草稿，
  // 只在收到第一次 revision 时锁定，后续 revision 不再覆盖
  const firstRevisionIndexRef = useRef<number | null>(null);
  // 本轮是否已收到 done：onComplete 据此避免覆盖已定的收尾文案
  const doneReceivedRef = useRef(false);
  // 本轮是否已收到服务端 error：onComplete 的兜底提示不能盖掉更具体的原因
  const errorReceivedRef = useRef(false);
  const currentToolIdRef = useRef<string>("");
  const pendingQueueRef = useRef<string[]>([]);
  // 本轮开始时间（毫秒）：消息落库时带上起点，也供消息内的计时使用
  const turnStartedAtRef = useRef(0);
  const interruptedRef = useRef(false);

  // 正在进行的那一轮请求属于哪个会话。切换会话时不再 abort：让它在后台跑完并落库，
  // 切回来还能看到完整回答；凡是非前台会话的事件只写 ref，不进当前消息列表。
  const threadIdRef = useRef<string>(threadId);
  const streamThreadRef = useRef<string | null>(null);
  const interruptPayloadRef = useRef<InterruptData | null>(null);
  const interruptThreadRef = useRef<string | null>(null);

  const resetAssistantState = useCallback(() => {
    assistantMsgRef.current = "";
    toolCallsRef.current = [];
    partsRef.current = [];
    currentToolIdRef.current = "";
    finalStartIndexRef.current = 0;
    firstRevisionIndexRef.current = null;
  }, []);

  // 重置所有瞬时 UI 状态（todo / 阶段）
  const resetTransientUi = useCallback(() => {
    setTodoItems([]);
    setTodoVisible(false);
    setPhase("idle");
    setPhaseLabel("");
  }, []);

  const updateAssistantMessage = useCallback(() => {
    // 后台会话（不是当前展示的）只累积文本，不渲染
    if (streamThreadRef.current !== threadIdRef.current) return;
    setMessages((prev) => {
      const lastMsg = prev[prev.length - 1];
      const updated: ChatMessage = {
        id: lastMsg?.role === "assistant" ? lastMsg.id : uuidv4(),
        role: "assistant",
        content: assistantMsgRef.current,
        toolCalls: [...toolCallsRef.current],
        // 复制一份：列表里保留的是快照，后续 ref 再变化不会回写已渲染的消息
        parts: [...partsRef.current],
        timestamp: Date.now(),
        startedAt: turnStartedAtRef.current || undefined,
        endedAt: undefined,
        finalStartIndex: finalStartIndexRef.current,
        firstRevisionIndex: firstRevisionIndexRef.current ?? undefined,
      };
      if (lastMsg?.role === "assistant") {
        return [...prev.slice(0, -1), updated];
      }
      return [...prev, updated];
    });
  }, []);

  /**
   * 给最后一条 assistant 消息盖上结束时间，供消息底部展示整轮用时。
   * 没有 assistant 消息（纯报错/空轮）时自然 no-op。
   */
  const markAssistantEnded = useCallback(() => {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role !== "assistant") return prev;
      const startedAt = last.startedAt ?? (turnStartedAtRef.current || undefined);
      return [
        ...prev.slice(0, -1),
        { ...last, startedAt, endedAt: last.endedAt ?? Date.now() },
      ];
    });
  }, []);

  /**
   * 更新单个工具调用，并让 parts 里的同一分段指向同一个新对象。
   * 必须两处同步：toolCalls 给旧组件/产物提取用，parts 给新渲染顺序用，
   * 只改一边会出现"列表里已完成、分段里还在转圈"的撕裂状态。
   */
  const updateToolCall = useCallback((id: string, patch: Partial<ToolCallInfo>) => {
    if (!id) return;
    const target = toolCallsRef.current.find((tc) => tc.id === id);
    // 找不到就保持原样：旧后端可能不带 id，此前的退化匹配已经处理过
    if (!target) return;
    const next: ToolCallInfo = { ...target, ...patch };
    toolCallsRef.current = toolCallsRef.current.map((tc) =>
      tc.id === id ? next : tc
    );
    partsRef.current = partsRef.current.map((p): MessagePart =>
      p.type === "tool" && p.call.id === id ? { type: "tool", call: next } : p
    );
  }, []);

  /**
   * 连接中断 / 服务端报错时，把停在 running 的工具统一收口为 error。
   * toolCalls 和 parts 必须同时改：前者给产物提取/旧组件，后者是当前渲染依据。
   * 返回是否真的存在未完成工具，供调用方决定是否提示"连接中断"。
   */
  const markStrandedTools = useCallback((): boolean => {
    const markError = (tc: ToolCallInfo): ToolCallInfo =>
      tc.status === "running"
        ? { ...tc, status: "error" as const, endedAt: tc.endedAt ?? Date.now() }
        : tc;
    const stranded =
      toolCallsRef.current.some((tc) => tc.status === "running") ||
      partsRef.current.some(
        (p) => p.type === "tool" && p.call.status === "running"
      );
    if (!stranded) return false;

    toolCallsRef.current = toolCallsRef.current.map(markError);
    partsRef.current = partsRef.current.map((p): MessagePart =>
      p.type === "tool" ? { type: "tool", call: markError(p.call) } : p
    );
    setMessages((prev) =>
      prev.map((msg) => {
        const hasRunning =
          msg.toolCalls?.some((tc) => tc.status === "running") ||
          msg.parts?.some(
            (p) => p.type === "tool" && p.call.status === "running"
          );
        if (!hasRunning) return msg;
        return {
          ...msg,
          toolCalls: msg.toolCalls?.map(markError),
          parts: msg.parts?.map((p): MessagePart =>
            p.type === "tool" ? { type: "tool", call: markError(p.call) } : p
          ),
        };
      })
    );
    return true;
  }, []);

  const handleEvent = useCallback(
    (event: SSEEvent) => {
      const foreground = streamThreadRef.current === threadIdRef.current;
      // 文本/工具/中断即使不在前台也要处理（继续累积），其余 UI 状态只在前台更新
      const alwaysHandle =
        event.type === "token" ||
        event.type.startsWith("tool_") ||
        event.type === "interrupt" ||
        event.type === "revision";
      if (!foreground && !alwaysHandle) return;

      // 只要事件被真正处理，就刷新"最后活跃时间"，供界面判断是否已经静默卡住。
      // 放在 switch 外统一维护，避免新增分支时漏写；只在当前展示的会话上记，
      // 后台会话的事件不该让前台看起来还在活动。
      if (foreground) setLastEventAt(Date.now());

      switch (event.type) {
        case "token":
          assistantMsgRef.current += event.content;
          // 与聚合正文同步维护分段：最后一段是正文就续写，否则另起一段。
          // 这样工具调用前后的文字不会黏成一段，顺序也被保留下来。
          {
            const last = partsRef.current[partsRef.current.length - 1];
            if (last && last.type === "text") {
              partsRef.current[partsRef.current.length - 1] = {
                type: "text",
                text: last.text + event.content,
              };
            } else {
              partsRef.current = [
                ...partsRef.current,
                { type: "text", text: event.content },
              ];
            }
          }
          // 已经在出正文了，进入"整理"阶段
          setActivityLabel("正在整理");
          updateAssistantMessage();
          break;

        case "revision": {
          // 终稿从最后一个分段之后开始：之前的正文退化为草稿，只在抽屉里展示
          finalStartIndexRef.current =
            typeof event.finalStartIndex === "number"
              ? event.finalStartIndex
              : partsRef.current.length;
          // 只在第一次修订时锁定"第一版草稿"的结束位置：
          // 后续修订不能改写它，否则答案区会跟着一起往前缩。
          // 0 是合法下标，必须用 === null 判断，不能用 ||。
          if (firstRevisionIndexRef.current === null) {
            firstRevisionIndexRef.current =
              typeof event.firstRevisionIndex === "number"
                ? event.firstRevisionIndex
                : finalStartIndexRef.current;
          }
          setActivityLabel("正在整理");
          updateAssistantMessage();
          break;
        }

        case "reasoning":
          // 思考内容不进正文，界面上只体现为"正在理解"
          setActivityLabel("正在理解");
          break;

        case "tool_start": {
          // 旧后端可能不带 id；前端生成一个本地 id，至少保证 args/result 能落到同一条
          const toolId = event.id || uuidv4();
          const tc: ToolCallInfo = {
            id: toolId,
            name: event.name,
            args: "",
            status: "running",
            startedAt: Date.now(),
          };
          toolCallsRef.current = [...toolCallsRef.current, tc];
          // 同时写入分段：工具在正文中的真实位置只能靠 parts 还原
          partsRef.current = [...partsRef.current, { type: "tool", call: tc }];
          currentToolIdRef.current = toolId;
          setActivityLabel(`正在执行 ${event.name}`);
          updateAssistantMessage();
          break;
        }

        case "tool_args": {
          // 参数是分片到达的，必须在现有参数后追加而不是覆盖
          const current = toolCallsRef.current.find(
            (tc) => tc.id === currentToolIdRef.current
          );
          if (current) {
            updateToolCall(current.id, { args: current.args + event.args });
          }
          updateAssistantMessage();
          break;
        }

        case "tool_result": {
          // 优先按 id 精确匹配；后端未带 id 时，退化为"当前运行实例"，
          // 再不行才按同名 + running 匹配（兼容旧后端）。
          const targetId =
            event.id ||
            currentToolIdRef.current ||
            toolCallsRef.current.find(
              (tc) => tc.name === event.name && tc.status === "running"
            )?.id;
          if (targetId) {
            updateToolCall(targetId, {
              result: event.content,
              status: "done",
              endedAt: Date.now(),
            });
          }
          setActivityLabel("正在整理");
          updateAssistantMessage();
          break;
        }

        case "tool_end": {
          updateToolCall(event.id, { status: "done", endedAt: Date.now() });
          updateAssistantMessage();
          break;
        }

        case "interrupt": {
          // 归一化：interrupt_type 可能出现在事件顶层、data 内，
          // 甚至被后端写成 type / action_type，这里统一收敛到 interrupt_type，
          // 保证 InterruptBanner 一定能识别出类型（否则界面会静默空白）
          // 后端把审批字段平铺在事件里（tool_name / order_data ...），并没有
          // 包一层 data；只读 event.data 会拿到空对象，审批卡片于是只剩标题。
          const rawData = (
            event.data && typeof event.data === "object" ? event.data : event
          ) as InterruptData & Record<string, unknown>;
          const type =
            rawData.interrupt_type ??
            (rawData.type as string | undefined) ??
            (rawData.action_type as string | undefined) ??
            event.interrupt_type ??
            "";
          const normalized: InterruptData = { ...rawData, interrupt_type: type };
          interruptedRef.current = true;
          interruptPayloadRef.current = normalized;
          interruptThreadRef.current = streamThreadRef.current;
          if (!foreground) break;   // 后台会话等切回来再展示审批卡
          setInterrupted(true);
          setInterruptData(normalized);
          break;
        }

        case "thinking":
          // 思考阶段只记录进详情面板；主文案统一收成"正在理解"
          if (event.status === "start") {
            setPhase("thinking");
            // 只保留"思考中"，不出现"深度思考"这样的措辞
            setPhaseLabel("思考中");
            setActivityLabel("正在理解");
          }
          break;

        case "phase": {
          // 后端可能发 result；前端阶段条只有 done，先做一次映射
          const nextPhase = event.phase === "result" ? "done" : event.phase;
          // 白名单校验，防止未知字符串写入阶段状态
          if (VALID_PHASES.has(nextPhase)) {
            setPhase(nextPhase as HarnessPhase);
          }
          // 阶段文案只进详情面板，不再覆盖胶囊主文案
          setPhaseLabel(event.label);
          break;
        }

        case "todo_update": {
          // 新格式：后端直接发送 todos 数组
          if (event.todos && Array.isArray(event.todos)) {
            const items: TodoItem[] = event.todos.map((t) => ({
              id: t.id || `todo-${Math.random().toString(36).slice(2)}`,
              content: t.content || "",
              status: (t.status as TodoItem["status"]) || "pending",
            }));
            setTodoItems(items);
            setTodoVisible(true);
          } else if (event.status_change === "executing") {
            // 执行阶段：将所有 pending 改为 in_progress
            setTodoItems((prev) =>
              prev.map((item) =>
                item.status === "pending" ? { ...item, status: "in_progress" as const } : item
              )
            );
          } else if (event.args) {
            // 兼容旧格式
            try {
              const args = JSON.parse(event.args);
              if (args.todos && Array.isArray(args.todos)) {
                const items: TodoItem[] = args.todos.map((t: Record<string, unknown>, i: number) => ({
                  id: (t.id as string) || `todo-${i}`,
                  content: (t.content as string) || "",
                  status: (t.status as TodoItem["status"]) || "pending",
                }));
                setTodoItems(items);
                setTodoVisible(true);
              }
            } catch {
              // 解析失败静默处理
            }
          }
          break;
        }

        case "done":
          // 记下已收到 done：随后的 onComplete 不能再改写收尾文案
          doneReceivedRef.current = true;
          setStreaming(false);
          // 服务端报错后也会补一个 done；此时不能把"出错了"改写成"已完成"
          if (!errorReceivedRef.current) {
            setActivityLabel(event.interrupted ? "等待你确认" : "已完成");
          }
          markAssistantEnded();
          setTodoItems((prev) =>
            prev.map((item) =>
              item.status !== "cancelled" ? { ...item, status: "complete" as const } : item
            )
          );
          if (errorReceivedRef.current) {
            // 失败轮次不推进阶段条，避免出现"已完成"的错误暗示
            break;
          }
          if (event.interrupted) {
            // 等审批不是"完成"：阶段条停在执行态并写明等待审批
            setInterrupted(true);
            setPhase("executing");
            setPhaseLabel("等待审批");
          } else {
            setPhase("done");
            setPhaseLabel("已完成");
          }
          // 排队消息不在这里发：done 之后服务端还要做最终落库，
          // 过早 start 下一轮会 abort 掉旧请求，导致 trace/会话标题丢失。
          // 统一等 onComplete（HTTP 流真正关闭）再发。
          break;

        case "error": {
          // 服务端显式报错：这一轮就是失败了，不必等 done 或连接超时
          errorReceivedRef.current = true;
          setError(event.message || "服务端返回错误");
          setStreaming(false);
          setActivityLabel("执行中断");
          markAssistantEnded();
          // 已经明确的失败不会再补结果，把 running 的工具就地收口，
          // 否则工具行会无限转圈，看起来像还在跑
          markStrandedTools();
          // 这里不调用 abort：让流的收尾交给 useSSE 的 onComplete，
          // 否则会和它自己的 AbortError 分支抢着收尾
          break;
        }
      }
    },
    [updateAssistantMessage, updateToolCall, markStrandedTools, markAssistantEnded]
  );

  const { start, abort } = useSSE({
    onEvent: handleEvent,
    onError: (err) => {
      if (streamThreadRef.current === threadIdRef.current) {
        setError(err.message);
        setStreaming(false);
        setActivityLabel("执行中断");
        markAssistantEnded();
        // 网络层错误不会走 done/onComplete，工具必须在这里收口
        markStrandedTools();
        // 错误轮次也可能已经落了一部分库，通知页面刷新历史
        setFinalizedAt(Date.now());
      }
      streamThreadRef.current = null;
    },
    onComplete: () => {
      const foreground = streamThreadRef.current === threadIdRef.current;
      streamThreadRef.current = null;
      // 后台会话结束时不要动当前界面的状态
      if (!foreground) return;
      setStreaming(false);
      setLastEventAt(Date.now());
      // 到这里后端 on_complete 已经跑完（或已失败），缓存再失效才不会拿到旧消息
      setFinalizedAt(Date.now());
      // 已收到 done 时 activityLabel 已经是终态（"已完成"/"等待你确认"），
      // 这里只兜住"连接正常关闭但没收到 done"的情况，避免文案停在"正在整理"
      if (!doneReceivedRef.current) setActivityLabel("");
      // 兜底盖结束时间：done 已盖过的话 markAssistantEnded 是幂等的
      markAssistantEnded();

      // 流结束但仍有工具停在 running（连接被掐断、后端进程重启等）：
      // 把状态收口并明确告知，否则工具行会一直转圈，看起来像"卡住"
      if (!interruptedRef.current && markStrandedTools()) {
        setError("连接中断，有工具调用没有返回结果，请重新发送这一轮");
      }

      // HTTP 流真正关闭后再发排队消息：done 只是"不再有新事件"，
      // 后端可能还在做最终落库；此时 start 新请求会 abort 旧请求。
      const q = pendingQueueRef.current;
      if (
        q.length > 0 &&
        doneReceivedRef.current &&
        !errorReceivedRef.current &&
        !interruptedRef.current
      ) {
        const next = q.shift()!;
        setPendingQueue([...q]);
        // 延迟一帧发送，确保 streaming 状态已更新；
        // 用 ref 调用最新的 doSend，避免陈旧闭包把消息发到旧 thread。
        // 50ms 内用户可能新建会话，必须再校验一次归属，不能把消息发到旧 thread。
        const scheduledThread = threadIdRef.current;
        setTimeout(() => {
          if (threadIdRef.current !== scheduledThread) return;
          doSendRef.current(next);
        }, 50);
      }

      // 整轮既没有正文、也没有进入中断 → 明确告知，避免"发消息毫无反应"。
      // 例外：工具结果里已经带出可下载产物，界面会用产物卡片兜底展示；
      // 以及服务端已经给了具体错误——那时候这条泛化提示只会盖掉真正原因。
      if (
        !assistantMsgRef.current &&
        !interruptedRef.current &&
        !errorReceivedRef.current &&
        !hasChatArtifact({
          content: assistantMsgRef.current,
          toolCalls: toolCallsRef.current,
        })
      ) {
        setError("未收到助手回复，请检查后端服务是否正常后重试");
      }
    },
  });

  // 实际的发送逻辑（非排队）
  const doSend = useCallback(
    (content: string) => {
      setError(null);
      setInterrupted(false);
      setInterruptData(null);
      interruptedRef.current = false;
      interruptPayloadRef.current = null;
      interruptThreadRef.current = null;
      resetTransientUi();
      resetAssistantState();
      // 新的一轮：活动文案、活跃时间、终稿分界、轮次起点全部重新起算
      doneReceivedRef.current = false;
      errorReceivedRef.current = false;
      finalStartIndexRef.current = 0;
      firstRevisionIndexRef.current = null;
      const now = Date.now();
      setActivityLabel("正在理解");
      setLastEventAt(now);
      turnStartedAtRef.current = now;

      // 这一轮的归属：后续事件据此判断要不要渲染到当前界面
      threadIdRef.current = threadId;
      streamThreadRef.current = threadId;

      const userMsg: ChatMessage = {
        id: uuidv4(),
        role: "user",
        content: content.trim(),
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming(true);
      // 立刻给出可见反馈：胶囊和阶段不等后端事件，
      // 否则从按下回车到首个 token 之间会有一两秒"什么都没发生"
      setPhase("thinking");
      setPhaseLabel("正在连接模型…");

      start((onChunk, signal) =>
        streamChat(
          {
            message: content.trim(),
            thread_id: threadId,
            user_id: USER_ID,
            username: USERNAME,
          },
          onChunk,
          signal
        )
      );
    },
    [threadId, start, resetAssistantState, resetTransientUi]
  );

  // 始终持有最新的 doSend，供 SSE done 事件中自动发送排队消息使用
  const doSendRef = useRef(doSend);
  doSendRef.current = doSend;

  const sendMessage = useCallback(
    (content: string) => {
      if (!content.trim()) return;

      if (streamThreadRef.current) {
        if (streamThreadRef.current !== threadIdRef.current) {
          // 另一个会话还在后台生成：不允许往当前会话发，避免两条流互相覆盖
          setError("另一个会话还在生成回复，请等它结束或切回该会话");
          return;
        }
        // 同一会话：加入排队队列，回答完后自动发送
        const q = [...pendingQueueRef.current, content.trim()];
        pendingQueueRef.current = q;
        setPendingQueue(q);
        return;
      }

      doSend(content.trim());
    },
    [streaming, doSend]
  );

  const resumeWith = useCallback(
    (resumeData: Record<string,unknown>) => {
      setInterrupted(false);
      setInterruptData(null);
      setError(null);
      interruptedRef.current = false;
      interruptPayloadRef.current = null;
      interruptThreadRef.current = null;
      // resume 是同一次执行的延续：保留中断前已输出的正文和工具调用，
      // 只把阶段条切回执行态（不能停在 done/"✅ 完成"却又在转圈）
      threadIdRef.current = threadId;
      streamThreadRef.current = threadId;
      setStreaming(true);
      setPhase("executing");
      setPhaseLabel("");
      // resume 是同一轮执行的延续：不重置轮次起点，只有它为空时（刷新页面后审批）才补一个
      doneReceivedRef.current = false;
      errorReceivedRef.current = false;
      setActivityLabel("正在执行");
      setLastEventAt(Date.now());
      if (turnStartedAtRef.current === 0) {
        turnStartedAtRef.current = Date.now();
      }

      start((onChunk, signal) =>
        resumeChat(
          { thread_id: threadId, resume_data: resumeData },
          onChunk,
          signal
        )
      );
    },
    [threadId, start]
  );

  const newChat = useCallback(() => {
    abort();
    streamThreadRef.current = null;
    interruptPayloadRef.current = null;
    interruptThreadRef.current = null;
    setMessages([]);
    setStreaming(false);
    setInterrupted(false);
    setInterruptData(null);
    setError(null);
    interruptedRef.current = false;
    const nextId = uuidv4();
    threadIdRef.current = nextId;
    setThreadId(nextId);
    setPendingQueue([]);
    pendingQueueRef.current = [];
    resetTransientUi();
    resetAssistantState();
    // 新会话没有进行中的轮次，时间与文案一并归零
    doneReceivedRef.current = false;
    errorReceivedRef.current = false;
    setActivityLabel("");
    setLastEventAt(0);
    turnStartedAtRef.current = 0;
    finalStartIndexRef.current = 0;
    firstRevisionIndexRef.current = null;
  }, [abort, resetAssistantState, resetTransientUi]);

  const loadThread = useCallback(
    (id: string, msgs: ChatMessage[], restoredInterrupt?: InterruptData | null) => {
      // 这里刻意不 abort：正在进行的那一轮继续在后台跑完并落库，
      // 切回来时能拿到完整回答，而不是被截断的半句话。
      const streamingThis = streamThreadRef.current === id;
      // 优先用内存里的实时中断（切换回来的正在跑的会话），
      // 否则用状态接口拉到的中断（刷新页面 / 进程重启后的待审批）
      const liveInterrupt =
        streamingThis &&
        interruptThreadRef.current === id &&
        interruptPayloadRef.current !== null;
      const pending = liveInterrupt
        ? interruptPayloadRef.current
        : restoredInterrupt ?? null;
      threadIdRef.current = id;
      setThreadId(id);
      // 非前台运行、也不在等待审批的历史会话，停在 running 的工具说明上次流
      // 已异常结束；装载时收口，否则刷新后工具行会永远转圈。
      // 等待审批时工具本来就应该保持运行态，不能被误标为 error。
      setMessages(
        streamingThis || pending ? msgs : msgs.map(markStrandedMessage)
      );
      if (pending) {
        interruptThreadRef.current = id;
        interruptPayloadRef.current = pending;
      } else {
        interruptThreadRef.current = null;
        interruptPayloadRef.current = null;
      }
      setStreaming(streamingThis);
      setInterrupted(!!pending);
      interruptedRef.current = !!pending;
      setInterruptData(pending);
      setError(null);
      setPendingQueue([]);
      pendingQueueRef.current = [];
      // 刻意不动 partsRef：后台正在生成的那一轮还要继续往分段里写，
      // 清空会让切回来时的消息丢掉已收到的分段
      if (streamingThis) {
        // 切回正在生成的会话：保留已累积的正文，继续往后渲染
        setTodoItems([]);
        setTodoVisible(false);
        setPhase("executing");
        setPhaseLabel("");
        // 主文案从最后一条 assistant 的 running 工具恢复；没有就当作在整理
        const lastAssistant = [...msgs]
          .reverse()
          .find((m) => m.role === "assistant");
        const runningTool = lastAssistant?.toolCalls?.find(
          (tc) => tc.status === "running"
        );
        setActivityLabel(
          runningTool ? `正在执行 ${runningTool.name}` : "正在整理"
        );
        setLastEventAt(Date.now());
        if (turnStartedAtRef.current === 0) {
          turnStartedAtRef.current = Date.now();
        }
      } else {
        resetTransientUi();
        setActivityLabel("");
        setLastEventAt(0);
        // 没有在跑的流就意味着没有进行中的轮次；
        // 历史消息的终稿分界来自消息自身的 finalStartIndex，这里只归零当前轮
        turnStartedAtRef.current = 0;
        finalStartIndexRef.current = 0;
        firstRevisionIndexRef.current = null;
      }
      if (!streamThreadRef.current) resetAssistantState();
    },
    [abort, resetAssistantState, resetTransientUi]
  );

  const dismissError = useCallback(() => setError(null), []);
  const showLoadError = useCallback(
    () => setError("历史消息加载失败，请检查网络后重试"),
    []
  );

  return {
    messages,
    streaming,
    interrupted,
    interruptData,
    threadId,
    error,
    todoItems,
    todoVisible,
    pendingQueue,
    phase,
    phaseLabel,
    activityLabel,
    lastEventAt,
    finalizedAt,
    // 供外部（线程缓存逻辑）读取"当前展示的是哪个会话"，不触发重渲染
    threadIdRef,
    sendMessage,
    resumeWith,
    newChat,
    loadThread,
    abort,
    dismissError,
    showLoadError,
  };
}

"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { v4 as uuidv4 } from "uuid";
import {
  ChatMessage,
  ToolCallInfo,
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

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
  const [interruptData, setInterruptData] = useState<InterruptData | null>(null);
  const [threadId, setThreadId] = useState<string>(uuidv4());
  const [error, setError] = useState<string | null>(null);
  const [todoItems, setTodoItems] = useState<TodoItem[]>([]);
  const [todoVisible, setTodoVisible] = useState(false);
  const [phase, setPhase] = useState<HarnessPhase>("idle");
  const [phaseLabel, setPhaseLabel] = useState("");
  const [pendingQueue, setPendingQueue] = useState<string[]>([]);

  const assistantMsgRef = useRef<string>("");
  const toolCallsRef = useRef<ToolCallInfo[]>([]);
  const currentToolIdRef = useRef<string>("");
  const pendingQueueRef = useRef<string[]>([]);
  const thinkingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
    currentToolIdRef.current = "";
  }, []);

  // 重置所有瞬时 UI 状态（todo / 阶段条 / 思考动画）
  const resetTransientUi = useCallback(() => {
    setThinking(false);
    setTodoItems([]);
    setTodoVisible(false);
    setPhase("idle");
    setPhaseLabel("");
    if (thinkingTimerRef.current) {
      clearTimeout(thinkingTimerRef.current);
      thinkingTimerRef.current = null;
    }
  }, []);

  // 组件卸载时清理遗留定时器
  useEffect(() => {
    return () => {
      if (thinkingTimerRef.current) clearTimeout(thinkingTimerRef.current);
    };
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
        timestamp: Date.now(),
      };
      if (lastMsg?.role === "assistant") {
        return [...prev.slice(0, -1), updated];
      }
      return [...prev, updated];
    });
  }, []);

  const handleEvent = useCallback(
    (event: SSEEvent) => {
      const foreground = streamThreadRef.current === threadIdRef.current;
      // 文本/工具/中断即使不在前台也要处理（继续累积），其余 UI 状态只在前台更新
      const alwaysHandle =
        event.type === "token" ||
        event.type.startsWith("tool_") ||
        event.type === "interrupt";
      if (!foreground && !alwaysHandle) return;

      switch (event.type) {
        case "token":
          assistantMsgRef.current += event.content;
          updateAssistantMessage();
          break;

        case "tool_start": {
          const tc: ToolCallInfo = {
            id: event.id,
            name: event.name,
            args: "",
            status: "running",
            startedAt: Date.now(),
          };
          toolCallsRef.current = [...toolCallsRef.current, tc];
          currentToolIdRef.current = event.id;
          updateAssistantMessage();
          break;
        }

        case "tool_args": {
          toolCallsRef.current = toolCallsRef.current.map((tc) =>
            tc.id === currentToolIdRef.current
              ? { ...tc, args: tc.args + event.args }
              : tc
          );
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
          toolCallsRef.current = toolCallsRef.current.map((tc) =>
            tc.id === targetId
              ? { ...tc, result: event.content, status: "done" as const }
              : tc
          );
          updateAssistantMessage();
          break;
        }

        case "tool_end": {
          toolCallsRef.current = toolCallsRef.current.map((tc) =>
            tc.id === event.id ? { ...tc, status: "done" as const } : tc
          );
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
          if (event.status === "start") {
            if (thinkingTimerRef.current) {
              clearTimeout(thinkingTimerRef.current);
              thinkingTimerRef.current = null;
            }
            setThinking(true);
            setPhase("thinking");
            setPhaseLabel("深度思考中");
          } else {
            // 延迟结束 thinking，保证动画至少显示 800ms；timer 可被清理
            if (thinkingTimerRef.current) clearTimeout(thinkingTimerRef.current);
            thinkingTimerRef.current = setTimeout(() => {
              setThinking(false);
              thinkingTimerRef.current = null;
            }, 800);
          }
          break;

        case "phase":
          // 白名单校验，防止未知字符串写入阶段状态
          if (VALID_PHASES.has(event.phase)) {
            setPhase(event.phase as HarnessPhase);
          }
          setPhaseLabel(event.label);
          break;

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
          setStreaming(false);
          setTodoItems((prev) =>
            prev.map((item) =>
              item.status !== "cancelled" ? { ...item, status: "complete" as const } : item
            )
          );
          if (event.interrupted) {
            // 等审批不是"完成"：阶段条停在执行态并写明等待审批
            setInterrupted(true);
            setPhase("executing");
            setPhaseLabel("等待审批");
          } else {
            setPhase("done");
            setPhaseLabel("已完成");
          }
          // 检查排队队列：进入审批中断时不自动发送，等用户处理完中断
          const q = pendingQueueRef.current;
          if (q.length > 0 && !event.interrupted) {
            const next = q.shift()!;
            setPendingQueue([...q]);
            // 延迟一帧发送，确保 streaming 状态已更新；
            // 用 ref 调用最新的 doSend，避免陈旧闭包把消息发到旧 thread
            setTimeout(() => doSendRef.current(next), 50);
          }
          break;
      }
    },
    [updateAssistantMessage]
  );

  const { start, abort } = useSSE({
    onEvent: handleEvent,
    onError: (err) => {
      if (streamThreadRef.current === threadIdRef.current) {
        setError(err.message);
        setStreaming(false);
      }
      streamThreadRef.current = null;
    },
    onComplete: () => {
      const foreground = streamThreadRef.current === threadIdRef.current;
      streamThreadRef.current = null;
      // 后台会话结束时不要动当前界面的状态
      if (!foreground) return;
      setStreaming(false);

      // 流结束但仍有工具停在 running（连接被掐断、后端进程重启等）：
      // 把状态收口并明确告知，否则工具行会一直转圈，看起来像"卡住"
      if (!interruptedRef.current) {
        const stranded = toolCallsRef.current.some((tc) => tc.status === "running");
        if (stranded) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.toolCalls?.some((tc) => tc.status === "running")
                ? {
                    ...msg,
                    toolCalls: msg.toolCalls.map((tc) =>
                      tc.status === "running"
                        ? { ...tc, status: "error" as const }
                        : tc
                    ),
                  }
                : msg
            )
          );
          setError("连接中断，有工具调用没有返回结果，请重新发送这一轮");
        }
      }

      // 整轮既没有正文、也没有进入中断 → 明确告知，避免"发消息毫无反应"。
      // 例外：工具结果里已经带出可下载产物，界面会用产物卡片兜底展示。
      if (
        !assistantMsgRef.current &&
        !interruptedRef.current &&
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
      // 立刻给出可见反馈：阶段条和思考动画不等后端事件，
      // 否则从按下回车到首个 token 之间会有一两秒"什么都没发生"
      setThinking(true);
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
  }, [abort, resetAssistantState, resetTransientUi]);

  const loadThread = useCallback(
    (id: string, msgs: ChatMessage[], restoredInterrupt?: InterruptData | null) => {
      // 这里刻意不 abort：正在进行的那一轮继续在后台跑完并落库，
      // 切回来时能拿到完整回答，而不是被截断的半句话。
      threadIdRef.current = id;
      setThreadId(id);
      setMessages(msgs);
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
      if (streamingThis) {
        // 切回正在生成的会话：保留已累积的正文，继续往后渲染
        setThinking(false);
        setTodoItems([]);
        setTodoVisible(false);
        setPhase("executing");
        setPhaseLabel("");
      } else {
        resetTransientUi();
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
    thinking,
    interrupted,
    interruptData,
    threadId,
    error,
    todoItems,
    todoVisible,
    pendingQueue,
    phase,
    phaseLabel,
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

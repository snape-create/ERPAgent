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
            event.id ??
            currentToolIdRef.current ??
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
            setPhaseLabel("💭 深度思考中");
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
          setPhase("done");
          setPhaseLabel("✅ 完成");
          setTodoItems((prev) =>
            prev.map((item) =>
              item.status !== "cancelled" ? { ...item, status: "complete" as const } : item
            )
          );
          if (event.interrupted) {
            setInterrupted(true);
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
      setError(err.message);
      setStreaming(false);
    },
    onComplete: () => {
      setStreaming(false);
      // 整轮既没有正文、也没有进入中断 → 明确告知，避免"发消息毫无反应"
      if (!assistantMsgRef.current && !interruptedRef.current) {
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
      resetTransientUi();
      resetAssistantState();

      const userMsg: ChatMessage = {
        id: uuidv4(),
        role: "user",
        content: content.trim(),
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming(true);

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

      if (streaming) {
        // 正在回答：加入排队队列，回答完后自动发送
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
      // resume 是同一次执行的延续：保留中断前已输出的正文和工具调用，
      // 只把阶段条切回执行态（不能停在 done/"✅ 完成"却又在转圈）
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
    setMessages([]);
    setStreaming(false);
    setInterrupted(false);
    setInterruptData(null);
    setError(null);
    interruptedRef.current = false;
    setThreadId(uuidv4());
    setPendingQueue([]);
    pendingQueueRef.current = [];
    resetTransientUi();
    resetAssistantState();
  }, [abort, resetAssistantState, resetTransientUi]);

  const loadThread = useCallback(
    (id: string, msgs: ChatMessage[]) => {
      abort();
      setThreadId(id);
      setMessages(msgs);
      setStreaming(false);
      setInterrupted(false);
      setInterruptData(null);
      setError(null);
      interruptedRef.current = false;
      setPendingQueue([]);
      pendingQueueRef.current = [];
      resetTransientUi();
      resetAssistantState();
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
    sendMessage,
    resumeWith,
    newChat,
    loadThread,
    abort,
    dismissError,
    showLoadError,
  };
}

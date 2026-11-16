"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "@/components/sidebar/Sidebar";
import ChatArea from "@/components/chat/ChatArea";
import { useChat } from "@/hooks/useChat";
import { useHistory } from "@/hooks/useHistory";
import { getMessages, getChatState } from "@/lib/api";
import { ChatMessage, InterruptData } from "@/lib/types";

/** 消息随带的待审批中断（与消息一起缓存） */
type MsgsWithInterrupt = ChatMessage[] & { _interrupt?: InterruptData | null };

type RawMsg = Record<string, unknown>;

function toChatMessages(raw: RawMsg[]): ChatMessage[] {
  return raw.map((m, idx) => ({
    id: (m.id as string) || `msg-${idx}`,
    role: (m.role as "user" | "assistant") || "assistant",
    content: (m.content as string) || "",
    source: m.source as string | undefined,
    toolCalls: m.toolCalls as ChatMessage["toolCalls"],
    timestamp: (m.timestamp as number) || Date.now(),
  }));
}

/** 内容签名：SWR 后台校验时，数据没变就不触发重新渲染 */
function signature(msgs: ChatMessage[]): string {
  return msgs
    .map((m) => `${m.role}:${m.content.length}:${(m.toolCalls ?? []).length}`)
    .join("|");
}

export default function Home() {
  const chat = useChat();
  const history = useHistory();
  const wasStreaming = useRef(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // 已加载会话的线程缓存：切回历史会话时零请求瞬时显示，
  // 后台静默校验（stale-while-revalidate），避免重复的全量 Markdown 挂载卡顿。
  const threadCacheRef = useRef(
    new Map<string, { msgs: ChatMessage[]; sig: string }>()
  );
  // 正在拉取的会话去重：点击与悬停预取不会重复发请求
  const inflightRef = useRef(new Map<string, Promise<ChatMessage[]>>());

  // 从网络拉取一个会话的消息（消息与状态接口并行，省一个往返）
  const fetchThread = useCallback((threadId: string) => {
    const inflight = inflightRef.current;
    const existing = inflight.get(threadId);
    if (existing) return existing;

    const task = (async () => {
      // 两个请求互不依赖，并行而非串行等待
      const [rawData, state] = await Promise.all([
        getMessages(threadId),
        getChatState(threadId).catch(() => null),
      ]);
      const msgs = toChatMessages(rawData.messages ?? rawData ?? []) as MsgsWithInterrupt;
      // 中断信息随消息一起缓存，命中缓存时也能补回审批卡片
      msgs._interrupt = state?.interrupt ?? null;
      return msgs;
    })();

    inflight.set(threadId, task);
    task.finally(() => inflight.delete(threadId));
    return task;
  }, []);

  // 聊天结束后自动刷新历史列表
  // 依赖 history.refresh（稳定）而不是整个 history 对象（每次渲染都是新对象，
  // 否则会导致 effect 每次渲染重复执行、疯狂请求 /api/history）。
  const refreshHistory = history.refresh;
  useEffect(() => {
    if (wasStreaming.current && !chat.streaming) {
      wasStreaming.current = false;
      // 这一轮刚落库，旧线程缓存已过时：下次切回时强制走网络拿新回答
      threadCacheRef.current.delete(chat.threadId);
      // 延迟 500ms 等待后端保存完成
      const timer = setTimeout(() => refreshHistory(), 500);
      return () => clearTimeout(timer);
    }
    wasStreaming.current = chat.streaming;
  }, [chat.streaming, chat.threadId, refreshHistory]);

  // 后台静默校验已展示的会话：有新内容才替换，无变化不触发重渲染
  const revalidate = useCallback(
    (threadId: string) => {
      fetchThread(threadId)
        .then((fresh) => {
          const interrupt = (fresh as MsgsWithInterrupt)._interrupt;
          const data = fresh as ChatMessage[];
          const sig = signature(data);
          const prevSig = threadCacheRef.current.get(threadId)?.sig;
          threadCacheRef.current.set(threadId, { msgs: data, sig });
          // 已切走、或后台校验发现内容没变：不触发重新渲染
          if (threadId !== chat.threadIdRef.current || prevSig === sig) return;
          chat.loadThread(threadId, data, interrupt ?? null);
        })
        .catch(() => {
          // 静默失败：缓存的旧内容继续可用
        });
    },
    [fetchThread, chat]
  );

  const handleSelectThread = useCallback(
    async (threadId: string) => {
      const cached = threadCacheRef.current.get(threadId);
      if (cached) {
        // 命中缓存：瞬时展示，不再等待网络，卡顿主要在这里消除
        const interrupt = (cached.msgs as MsgsWithInterrupt)._interrupt;
        chat.loadThread(threadId, cached.msgs, interrupt ?? null);
        revalidate(threadId);
        return;
      }
      try {
        const msgs = await fetchThread(threadId);
        const interrupt = (msgs as MsgsWithInterrupt)._interrupt;
        threadCacheRef.current.set(threadId, {
          msgs,
          sig: signature(msgs),
        });
        // 请求期间用户可能已点了别的会话：只在仍是目标会话时装载
        if (threadId === chat.threadIdRef.current) {
          chat.loadThread(threadId, msgs, interrupt ?? null);
        }
      } catch {
        // 交给 ChatArea 的错误条展示
        chat.showLoadError?.();
      }
    },
    [chat, fetchThread, revalidate]
  );

  // 悬停历史项时预取（触屏聚焦也触发），点击时大概率已命中缓存
  const handlePrefetchThread = useCallback(
    (threadId: string) => {
      if (threadCacheRef.current.has(threadId)) return;
      fetchThread(threadId)
        .then((msgs) => {
          threadCacheRef.current.set(threadId, {
            msgs,
            sig: signature(msgs),
          });
        })
        .catch(() => {
          // 预取失败静默处理
        });
    },
    [fetchThread]
  );

  const handleDeleteThread = useCallback(
    (threadId: string) => {
      threadCacheRef.current.delete(threadId);
      history.remove(threadId);
      if (threadId === chat.threadId) {
        chat.newChat();
      }
    },
    [history, chat]
  );

  const handleSupplement = useCallback(
    (text: string) => {
      chat.resumeWith({ supplement: text });
    },
    [chat]
  );

  const handleApprove = useCallback(() => {
    chat.resumeWith({ decisions: [{ type: "approve" }] });
  }, [chat]);

  const handleReject = useCallback(() => {
    chat.resumeWith({ decisions: [{ type: "reject" }] });
  }, [chat]);

  const handleEdit = useCallback(
    (args: Record<string, unknown>, toolName: string) => {
      // 后端据此补出框架要求的 edited_action，并把参数包回 order_data
      chat.resumeWith({ decisions: [{ type: "edit", tool_name: toolName, args }] });
    },
    [chat]
  );

  return (
    <div className="flex h-dvh overflow-hidden bg-paper">
      <Sidebar
        conversations={history.conversations}
        activeThreadId={chat.threadId}
        searchQuery={history.searchQuery}
        onSearchChange={history.setSearchQuery}
        onNewChat={chat.newChat}
        onSelectThread={handleSelectThread}
        onPrefetchThread={handlePrefetchThread}
        onDeleteThread={handleDeleteThread}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <ChatArea
        messages={chat.messages}
        streaming={chat.streaming}
        thinking={chat.thinking}
        interrupted={chat.interrupted}
        interruptData={chat.interruptData}
        todoItems={chat.todoItems}
        todoVisible={chat.todoVisible}
        pendingQueue={chat.pendingQueue}
        phase={chat.phase}
        phaseLabel={chat.phaseLabel}
        error={chat.error}
        onDismissError={chat.dismissError}
        onOpenSidebar={() => setSidebarOpen(true)}
        onSend={chat.sendMessage}
        onSupplement={handleSupplement}
        onApprove={handleApprove}
        onReject={handleReject}
        onEdit={handleEdit}
      />
    </div>
  );
}

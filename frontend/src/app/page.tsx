"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Sidebar from "@/components/sidebar/Sidebar";
import ChatArea from "@/components/chat/ChatArea";
import { useChat } from "@/hooks/useChat";
import { useHistory } from "@/hooks/useHistory";
import { getMessages } from "@/lib/api";
import { ChatMessage } from "@/lib/types";

export default function Home() {
  const chat = useChat();
  const history = useHistory();
  const wasStreaming = useRef(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // 聊天结束后自动刷新历史列表
  // 依赖 history.refresh（稳定）而不是整个 history 对象（每次渲染都是新对象，
  // 否则会导致 effect 每次渲染重复执行、疯狂请求 /api/history）。
  const refreshHistory = history.refresh;
  useEffect(() => {
    if (wasStreaming.current && !chat.streaming) {
      wasStreaming.current = false;
      // 延迟 500ms 等待后端保存完成
      const timer = setTimeout(() => refreshHistory(), 500);
      return () => clearTimeout(timer);
    }
    wasStreaming.current = chat.streaming;
  }, [chat.streaming, refreshHistory]);

  const handleSelectThread = useCallback(
    async (threadId: string) => {
      try {
        const data = await getMessages(threadId);
        const msgs: ChatMessage[] = (data.messages || data || []).map(
          (m: Record<string, unknown>, idx: number) => ({
            id: (m.id as string) || `msg-${idx}`,
            role: (m.role as "user" | "assistant") || "assistant",
            content: (m.content as string) || "",
            source: m.source as string | undefined,
            toolCalls: m.toolCalls as ChatMessage["toolCalls"],
            timestamp: (m.timestamp as number) || Date.now(),
          })
        );
        chat.loadThread(threadId, msgs);
      } catch {
        // 交给 ChatArea 的错误条展示
        chat.showLoadError?.();
      }
    },
    [chat]
  );

  const handleDeleteThread = useCallback(
    (threadId: string) => {
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

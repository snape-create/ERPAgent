"use client";

import { Conversation } from "@/lib/types";
import Logo from "./Logo";
import NewChatButton from "./NewChatButton";
import SearchBox from "./SearchBox";
import HistoryList from "./HistoryList";
import McpPanel from "@/components/mcp/McpPanel";

interface Props {
  conversations: Conversation[];
  activeThreadId: string;
  searchQuery: string;
  onSearchChange: (v: string) => void;
  onNewChat: () => void;
  onSelectThread: (id: string) => void;
  onDeleteThread: (id: string) => void;
  // 移动端抽屉状态
  mobileOpen: boolean;
  onClose: () => void;
}

export default function Sidebar({
  conversations,
  activeThreadId,
  searchQuery,
  onSearchChange,
  onNewChat,
  onSelectThread,
  onDeleteThread,
  mobileOpen,
  onClose,
}: Props) {
  // 移动端点击"新建会话/选择会话"后自动收起抽屉
  const handleNewChat = () => {
    onNewChat();
    onClose();
  };
  const handleSelect = (id: string) => {
    onSelectThread(id);
    onClose();
  };

  return (
    <>
      {/* 移动端遮罩 */}
      <div
        onClick={onClose}
        aria-hidden={!mobileOpen}
        className={`fixed inset-0 z-40 bg-ink/35 transition-opacity duration-200 ease-quiet md:hidden ${
          mobileOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      />

      <aside
        className={`fixed md:static z-50 md:z-auto top-0 left-0 w-[264px] h-dvh flex flex-col bg-sunken border-r border-hairline shrink-0 transition-transform duration-200 ease-quiet ${
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        }`}
      >
        <Logo />

        {/* 新建与搜索共用 12px 内边距，和历史行同一条左边缘 */}
        <div className="px-3">
          <NewChatButton onClick={handleNewChat} />
        </div>

        <div className="px-3 pt-2">
          <SearchBox value={searchQuery} onChange={onSearchChange} />
        </div>

        <div className="flex items-center justify-between px-4 pt-6 pb-2">
          <span className="text-[12px] text-ink-3">历史会话</span>
          <span className="font-mono text-[11px] text-ink-3 tnum">
            {conversations.length}
          </span>
        </div>

        <HistoryList
          conversations={conversations}
          activeThreadId={activeThreadId}
          onSelect={handleSelect}
          onDelete={onDeleteThread}
        />

        <McpPanel />

        <div className="px-4 py-3">
          <span className="font-mono text-[11px] text-ink-3">ERP Agent · v1.0.0</span>
        </div>
      </aside>
    </>
  );
}
